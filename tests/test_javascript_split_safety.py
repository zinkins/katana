from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from katana import (
    SourceSplitMove,
    analyze_source_file,
    apply_source_split,
    outline_source_file,
    plan_source_split,
)
from katana.cli import main


def test_commonjs_split_preserves_public_export_and_route_order(tmp_path: Path) -> None:
    source = tmp_path / "models.js"
    source.write_text(
        "const calls = [];\n"
        "const router = { get: (path, fn) => calls.push([path, fn()]) };\n"
        "function buildArtifactEtag() { return 'etag'; }\n"
        "router.get('/first', buildArtifactEtag);\n"
        "router.get('/second', () => 'second');\n"
        "module.exports = { calls, buildArtifactEtag };\n",
        encoding="utf-8",
    )
    target = tmp_path / "etag.js"
    apply_source_split(source, (SourceSplitMove(target, ("buildArtifactEtag",)),))
    result = subprocess.run(
        [
            "node",
            "-e",
            (
                "const m = require(process.argv[1]); "
                "console.log(JSON.stringify([m.buildArtifactEtag(), m.calls, Object.keys(m)]));"
            ),
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "etag",
        [["/first", "etag"], ["/second", "second"]],
        ["calls", "buildArtifactEtag"],
    ]
    assert "router.get" not in target.read_text(encoding="utf-8")
    routes = [s for s in analyze_source_file(source).symbols if "route_registration" in s.tags]
    assert len(routes) == 2
    assert all(s.constraints.order_policy == "preserve" for s in routes)


@pytest.mark.parametrize("structural", [False, True])
def test_function_does_not_capture_following_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, structural: bool
) -> None:
    if structural:
        monkeypatch.setattr("katana.outline.outline_typescript_with_ast", lambda path: None)
    source = tmp_path / "routes.js"
    source.write_text(
        "function sendCacheableArtifact() {\n  return `brace }`;\n}\n"
        "router.get('/artifact', sendCacheableArtifact);\n"
        "function other() { return 2; }\n",
        encoding="utf-8",
    )
    symbol = outline_source_file(source)[0]
    assert symbol.end_line == 3


def test_same_line_route_is_not_moved_with_function(tmp_path: Path) -> None:
    source = tmp_path / "routes.cjs"
    source.write_text(
        "function helper() { return 1; } module.exports = helper;\n", encoding="utf-8"
    )
    apply_source_split(source, (SourceSplitMove(tmp_path / "helper.cjs", ("helper",)),))
    result = subprocess.run(
        ["node", "-e", "console.log(require(process.argv[1])());", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


@pytest.mark.parametrize("check", [False, True])
def test_missing_dependency_rejects_plan_and_dry_run(
    tmp_path: Path, check: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "routes.cjs"
    original = (
        "const router = {};\nfunction helper() { return router; }\nmodule.exports = helper;\n"
    )
    source.write_text(original, encoding="utf-8")
    target = tmp_path / "helper.cjs"
    if check:
        with pytest.raises(ValueError, match="router"):
            main(["apply", str(source), "--move", "helper.cjs:helper", "--check", "--json"])
    else:
        with pytest.raises(ValueError, match="router"):
            plan_source_split(source, (SourceSplitMove(target, ("helper",)),))
    assert source.read_text(encoding="utf-8") == original
    assert not target.exists()


def test_map_symbol_id_works_in_plan_and_apply(tmp_path: Path) -> None:
    source = tmp_path / "helpers.mjs"
    source.write_text("export function helper() { return 1; }\n", encoding="utf-8")
    symbol_id = analyze_source_file(source).symbols[0].id
    moves = (SourceSplitMove(tmp_path / "helper.mjs", (symbol_id,)),)
    assert plan_source_split(source, moves).moved_symbols == ("helper",)
    apply_source_split(source, moves)
    assert "export function helper" in moves[0].target.read_text(encoding="utf-8")


def test_map_id_works_in_cli_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "helpers.mjs"
    original = "export function helper() { return 1; }\n"
    source.write_text(original, encoding="utf-8")
    symbol_id = analyze_source_file(source).symbols[0].id
    main(["apply", str(source), "--move", f"helper.mjs:{symbol_id}", "--check", "--json"])
    assert json.loads(capsys.readouterr().out)["check"]["ok"] is True
    assert source.read_text(encoding="utf-8") == original


def test_commonjs_dependency_closure_rebases_require(tmp_path: Path) -> None:
    (tmp_path / "value.cjs").write_text("module.exports = 42;\n", encoding="utf-8")
    source = tmp_path / "helpers.cjs"
    source.write_text(
        "const value = require('./value.cjs');\n"
        "function helper() { return value; }\n"
        "module.exports = { alias: helper };\n",
        encoding="utf-8",
    )
    target = tmp_path / "nested" / "helper.cjs"
    apply_source_split(source, (SourceSplitMove(target, ("value", "helper")),))
    result = subprocess.run(
        ["node", "-e", "console.log(require(process.argv[1]).alias());", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "42"


def test_destructured_dependency_must_move_with_consumer(tmp_path: Path) -> None:
    source = tmp_path / "helpers.cjs"
    source.write_text(
        "const { requireModel } = require('./repo.cjs');\n"
        "function helper() { return requireModel(); }\n"
        "module.exports = helper;\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="requireModel"):
        plan_source_split(source, (SourceSplitMove(tmp_path / "helper.cjs", ("helper",)),))


def test_shared_declaration_cannot_move_only_one_binding(tmp_path: Path) -> None:
    source = tmp_path / "helpers.cjs"
    source.write_text(
        "const first = 1, second = 2;\nmodule.exports = { first, second };\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="shared declaration"):
        apply_source_split(source, (SourceSplitMove(tmp_path / "first.cjs", ("first",)),))


def test_route_registration_move_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "routes.cjs"
    source.write_text("const router = {};\nrouter.get('/one', () => 1);\n", encoding="utf-8")
    route = next(s for s in outline_source_file(source) if "route_registration" in s.tags)
    with pytest.raises(ValueError, match="execution order"):
        plan_source_split(source, (SourceSplitMove(tmp_path / "route.cjs", (route.name,)),))


def test_commonjs_fails_closed_without_ast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "routes.cjs"
    source.write_text(
        "function helper() { return 1; } module.exports = helper;\n", encoding="utf-8"
    )
    monkeypatch.setattr("katana.outline.outline_typescript_with_ast", lambda path: None)
    with pytest.raises(ValueError, match="compiler AST"):
        apply_source_split(source, (SourceSplitMove(tmp_path / "helper.cjs", ("helper",)),))


def test_commonjs_dependency_cycle_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "helpers.cjs"
    source.write_text(
        "function first(n) { return n ? second(n - 1) : 1; }\n"
        "function second(n) { return n ? first(n - 1) : 2; }\n"
        "module.exports = first;\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="CommonJS dependency cycle"):
        plan_source_split(
            source,
            (
                SourceSplitMove(tmp_path / "first.cjs", ("first",)),
                SourceSplitMove(tmp_path / "second.cjs", ("second",)),
            ),
        )


def test_shared_require_declaration_moves_once(tmp_path: Path) -> None:
    (tmp_path / "values.cjs").write_text(
        "module.exports = { first: 1, second: 2 };\n", encoding="utf-8"
    )
    source = tmp_path / "helpers.cjs"
    source.write_text(
        "const { first, second } = require('./values.cjs');\n"
        "function helper() { return first + second; }\n"
        "module.exports = helper;\n",
        encoding="utf-8",
    )
    target = tmp_path / "nested" / "helper.cjs"
    apply_source_split(source, (SourceSplitMove(target, ("first", "second", "helper")),))
    result = subprocess.run(
        ["node", "-e", "console.log(require(process.argv[1])());", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "3"
    assert target.read_text(encoding="utf-8").count("const { first, second }") == 1
    assert target.read_text(encoding="utf-8").count("../values.cjs") == 1
