from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from katana import (
    SourceSplitMove,
    analyze_source_file,
    apply_source_split,
    outline_source_file,
    plan_source_split,
    source_parser_capabilities,
)

_PROBE = Path(__file__).with_suffix(".mjs")
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None
    or source_parser_capabilities(_PROBE)["parser"] != "typescript_compiler_ast",
    reason="TypeScript compiler bridge is required",
)


def test_node_test_registrations_move_as_ordered_side_effect_modules(tmp_path: Path) -> None:
    # Given a Node test module with shared setup and two top-level suites.
    source = tmp_path / "panel.test.mjs"
    source.write_text(
        'import { describe, it } from "node:test";\n\n'
        "function setup() {\n  return 42;\n}\n\n"
        'describe("first suite", () => {\n'
        '  it("uses setup", () => {\n    if (setup() !== 42) throw new Error("bad setup");\n  });\n'
        "});\n\n"
        'describe("second suite", () => {\n'
        '  it("still registers second", () => {});\n'
        "});\n",
        encoding="utf-8",
    )
    symbols = outline_source_file(source)
    registrations = [symbol for symbol in symbols if "test_registration" in symbol.tags]
    constraints = [
        symbol.constraints
        for symbol in analyze_source_file(source).symbols
        if "test_registration" in symbol.tags
    ]

    # When the setup and both registration blocks move in one explicit plan.
    apply_source_split(
        source,
        (
            SourceSplitMove(tmp_path / "panel.fixtures.mjs", ("setup",)),
            SourceSplitMove(
                tmp_path / "panel.first-suite.mjs",
                (registrations[0].name,),
            ),
            SourceSplitMove(
                tmp_path / "panel.second-suite.mjs",
                (registrations[1].name,),
            ),
        ),
        keep_compat_imports=False,
    )
    result = subprocess.run(
        ["node", "--test", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then the map exposed both suites and runtime registration stayed equivalent.
    assert [symbol.kind for symbol in registrations] == [
        "TestRegistration",
        "TestRegistration",
    ]
    assert [symbol.meta["title"] for symbol in registrations] == [
        "first suite",
        "second suite",
    ]
    assert [constraint.movability for constraint in constraints] == [
        "required_group",
        "required_group",
    ]
    assert all(constraint.order_policy == "preserve" for constraint in constraints)
    kept = source.read_text(encoding="utf-8")
    assert kept.index('import "./panel.first-suite.mjs";') < kept.index(
        'import "./panel.second-suite.mjs";'
    )
    assert 'describe("first suite"' not in kept
    assert 'describe("first suite"' in (tmp_path / "panel.first-suite.mjs").read_text()
    assert 'describe("second suite"' in (tmp_path / "panel.second-suite.mjs").read_text()
    assert result.returncode == 0, result.stderr
    assert "tests 2" in result.stdout
    assert "suites 2" in result.stdout


@pytest.mark.parametrize(
    "nested_body",
    [
        '  function render() {\n    return 1;\n  }\n  it("runs", () => {});',
        '  it("runs", () => {\n    const value = 1;\n    if (!value) throw new Error("bad");\n  });',
    ],
    ids=["function", "const"],
)
def test_node_test_registration_move_preserves_nested_declarations(
    tmp_path: Path,
    nested_body: str,
) -> None:
    # Given a Node test registration whose callback declares local state.
    source = tmp_path / "panel.test.mjs"
    target = tmp_path / "panel.suite.mjs"
    source_text = (
        'import { describe, it } from "node:test";\n\n'
        'describe("suite", () => {\n'
        f"{nested_body}\n"
        "});\n"
    )
    source.write_text(source_text, encoding="utf-8")
    registration = next(
        symbol for symbol in outline_source_file(source) if "test_registration" in symbol.tags
    )

    # When the complete registration moves to a side-effect module.
    apply_source_split(
        source,
        (SourceSplitMove(target, (registration.name,)),),
    )

    # Then Katana preserves every nested declaration byte for byte.
    assert nested_body in target.read_text(encoding="utf-8")


def test_node_test_registration_entry_does_not_start_with_blank_line(tmp_path: Path) -> None:
    # Given a test module whose imports are followed by a blank separator line.
    source = tmp_path / "panel.test.mjs"
    source.write_text(
        'import { describe, it } from "node:test";\n\n'
        'describe("first suite", () => {\n  it("runs", () => {});\n});\n\n'
        'describe("second suite", () => {\n  it("runs", () => {});\n});\n',
        encoding="utf-8",
    )
    registrations = [
        symbol for symbol in outline_source_file(source) if "test_registration" in symbol.tags
    ]

    # When every registration moves and the original imports become unused.
    apply_source_split(
        source,
        (
            SourceSplitMove(tmp_path / "panel.first.mjs", (registrations[0].name,)),
            SourceSplitMove(tmp_path / "panel.second.mjs", (registrations[1].name,)),
        ),
    )

    # Then the entry holds only the ordered side-effect imports, without a leading blank line.
    assert source.read_text(encoding="utf-8") == (
        'import "./panel.first.mjs";\nimport "./panel.second.mjs";\n'
    )


def test_node_test_registration_accepts_named_callback(tmp_path: Path) -> None:
    # Given a top-level suite registered with an imported callback.
    source = tmp_path / "panel.test.mjs"
    source.write_text(
        'import { describe } from "node:test";\n'
        'import { suite } from "./panel.suite.mjs";\n\n'
        'describe("named suite", suite);\n',
        encoding="utf-8",
    )

    # When Katana outlines the module.
    registrations = [
        symbol for symbol in outline_source_file(source) if "test_registration" in symbol.tags
    ]

    # Then the registration remains directly selectable.
    assert [symbol.meta["title"] for symbol in registrations] == ["named suite"]


def test_node_test_registration_requires_the_complete_ordered_group(tmp_path: Path) -> None:
    # Given three top-level suites whose registration order is observable.
    source = tmp_path / "panel.test.mjs"
    source.write_text(
        'import { describe } from "node:test";\n\n'
        'describe("first", () => {});\n'
        'describe("second", () => {});\n'
        'describe("third", () => {});\n',
        encoding="utf-8",
    )
    registrations = [
        symbol for symbol in outline_source_file(source) if "test_registration" in symbol.tags
    ]

    # When only one suite moves, the plan cannot preserve module registration.
    with pytest.raises(ValueError, match="all top-level node:test registrations"):
        plan_source_split(
            source,
            (SourceSplitMove(tmp_path / "panel.suites.mjs", (registrations[0].name,)),),
        )

    # Then interleaving two targets is rejected too.
    with pytest.raises(ValueError, match="contiguous source-order groups"):
        plan_source_split(
            source,
            (
                SourceSplitMove(
                    tmp_path / "panel.outer.mjs",
                    (registrations[0].name, registrations[2].name),
                ),
                SourceSplitMove(
                    tmp_path / "panel.middle.mjs",
                    (registrations[1].name,),
                ),
            ),
        )


def test_node_test_registration_requires_source_local_dependencies(tmp_path: Path) -> None:
    # Given a suite callback that closes over a source-local helper.
    source = tmp_path / "panel.test.mjs"
    source.write_text(
        'import { describe } from "node:test";\n\n'
        "function setup() { return 42; }\n\n"
        'describe("suite", () => { setup(); });\n',
        encoding="utf-8",
    )
    registration = next(
        symbol for symbol in outline_source_file(source) if "test_registration" in symbol.tags
    )

    # When the registration moves without its helper, the ESM cycle would be unsafe.
    with pytest.raises(ValueError, match="source-local dependencies must move"):
        plan_source_split(
            source,
            (SourceSplitMove(tmp_path / "panel.suites.mjs", (registration.name,)),),
        )


def test_node_test_registration_rejects_unmodeled_top_level_effects(
    tmp_path: Path,
) -> None:
    # Given a side effect whose position before describe is observable.
    source = tmp_path / "panel.test.mjs"
    source.write_text(
        'import { describe } from "node:test";\n\n'
        "initializeEnvironment();\n"
        'describe("suite", () => {});\n',
        encoding="utf-8",
    )
    registration = next(
        symbol for symbol in outline_source_file(source) if "test_registration" in symbol.tags
    )

    # When the suite is planned for a side-effect module, order cannot be proven safe.
    with pytest.raises(ValueError, match="unmodeled top-level executable statements"):
        plan_source_split(
            source,
            (SourceSplitMove(tmp_path / "panel.suites.mjs", (registration.name,)),),
        )
