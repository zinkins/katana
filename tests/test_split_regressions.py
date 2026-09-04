from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from katana import SourceSplitMove, apply_source_split, outline_source_file
from katana.cli import main
from katana.toolchains import (
    AdapterKind,
    Diagnostic,
    ToolchainContext,
    temporary_toolchain_adapter,
)


@pytest.mark.parametrize("compat", [True, False])
@pytest.mark.parametrize(
    "expression", ["register();", "[1].forEach(() => register());", "console.log(`${register()}`);"]
)
def test_top_level_calls_keep_local_bindings(tmp_path: Path, compat: bool, expression: str) -> None:
    # Given a hoisted helper used outside any named declaration.
    if shutil.which("node") is None:
        pytest.skip("Node is required for runtime verification")
    (tmp_path / "anchor.mjs").write_text("")
    source = tmp_path / "suite.mjs"
    source.write_text(
        f"import './anchor.mjs';\n{expression}\n\n"
        "function register() {\n  console.log('registered');\n}\n"
    )
    target = tmp_path / "helpers.mjs"

    # When the helper is moved with or without compatibility re-exports.
    apply_source_split(
        source, (SourceSplitMove(target, ("register",)),), keep_compat_imports=compat
    )
    result = subprocess.run(["node", str(source)], capture_output=True, text=True, check=False)

    # Then the entry still executes the helper once.
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines().count("registered") == 1
    assert ("export { register }" in source.read_text()) is compat


@pytest.mark.parametrize("compat", [True, False])
def test_literal_mentions_do_not_create_local_bindings(tmp_path: Path, compat: bool) -> None:
    # Given references only in comments, strings, a regex and raw template text.
    source = tmp_path / "suite.mjs"
    source.write_text(
        "// register\nconsole.log('register', /register/, `register`);\n\n"
        "function register() {\n  return 1;\n}\n"
    )
    target = tmp_path / "helpers.mjs"

    # When the unused helper moves.
    apply_source_split(
        source, (SourceSplitMove(target, ("register",)),), keep_compat_imports=compat
    )

    # Then compatibility exports do not require a local import.
    assert "import { register }" not in source.read_text()


@pytest.mark.parametrize("fallback", [True, False])
def test_css_comments_travel_with_the_following_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback: bool,
) -> None:
    # Given a documented prefix rule followed by separately documented rules.
    if fallback:
        monkeypatch.setattr("katana.outline.outline_css_with_ast", lambda _path: None)
    source = tmp_path / "style.css"
    moved = "/* Dimensions. */\n/* Keep this explanation. */\n.box {\n  width: 10px;\n}\n"
    remaining = "/* Labels. */\n.label {\n  color: red;\n}\n/* Footer. */\n"
    source.write_text(moved + "\n" + remaining)
    target = tmp_path / "dimensions.css"
    symbol = outline_source_file(source)[0]

    # When the prefix rule moves.
    apply_source_split(source, (SourceSplitMove(target, (symbol.name,)),))

    # Then each comment remains beside its owner and appears exactly once.
    assert target.read_text().strip() == moved.strip()
    assert remaining in source.read_text()
    assert "Dimensions" not in source.read_text()
    assert "Labels" not in target.read_text()


class RecordingTool:
    extensions = (".py",)
    default_enabled = False

    def __init__(self, kind: AdapterKind, calls: list[str]) -> None:
        self.id = f"regression-{kind}"
        self.kind = kind
        self.calls = calls

    def detect(self, context: ToolchainContext) -> None:
        return None

    def fix(self, files: list[Path]) -> list[Diagnostic]:
        self.calls.append(f"{self.kind}.fix")
        return []

    def check(self, files: list[Path]) -> list[Diagnostic]:
        assert all(path.exists() for path in files)
        self.calls.append(f"{self.kind}.check")
        return []


def test_no_lint_fix_still_runs_requested_typecheck(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given independently registered mutation and checker adapters.
    source = tmp_path / "source.py"
    source.write_text("def moved():\n    return 1\n")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"moves": [{"target": "helper.py", "symbols": ["moved"]}]}))
    calls: list[str] = []
    checker = RecordingTool("checker", calls)
    formatter = RecordingTool("formatter", calls)
    with temporary_toolchain_adapter(checker), temporary_toolchain_adapter(formatter):
        # When typechecking is requested while fixes are disabled.
        main(
            [
                "apply",
                str(source),
                "--plan-file",
                str(plan),
                "--no-lint-fix",
                "--typecheck",
                "--json",
            ]
        )

    # Then only the checker runs, and its result is reported.
    payload = json.loads(capsys.readouterr().out)
    assert calls == ["checker.check"]
    assert checker.id in payload["lint"]["checkers"]
    assert payload["lint"]["formatters"] == []
    assert payload["lint"]["linters"] == []
