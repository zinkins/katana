from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from katana import SourceSplitMove, external, outline_source_file
from katana.cli_output import _check_apply_source_split


@pytest.mark.parametrize(
    "package,suffix,text",
    [
        ("postcss", ".css", ".box,\n.label {\n  color: red;\n}\n"),
        ("typescript", ".ts", "export function moved() {\n  return 1;\n}\n"),
    ],
)
def test_dry_run_uses_original_project_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package: str,
    suffix: str,
    text: str,
) -> None:
    # Given a real parser installed only in the source project's dependency tree.
    if shutil.which("node") is None:
        pytest.skip("Node is required for parser verification")
    resolved = subprocess.run(
        ["node", "-p", f"require.resolve({json.dumps(package)})"],
        capture_output=True,
        text=True,
        check=False,
    )
    if resolved.returncode:
        pytest.skip(f"Optional {package} dependency is unavailable")
    project = tmp_path / "project"
    dependency = project / "node_modules" / package
    dependency.mkdir(parents=True)
    (dependency / "index.js").write_text(
        f"module.exports = require({json.dumps(resolved.stdout.strip())});\n"
    )
    (project / "package.json").write_text("{}")
    source_dir = project / "src"
    source_dir.mkdir()
    source = source_dir / f"source{suffix}"
    source.write_text(text)
    target = source_dir / "parts" / f"moved{suffix}"

    # Isolate the bridge from optional dependencies installed beside this checkout.
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    for bridge_file in external._NODE_BRIDGE.parent.glob("*.mjs"):
        shutil.copy2(bridge_file, bridge_dir / bridge_file.name)
    monkeypatch.setattr(external, "_NODE_BRIDGE", bridge_dir / "ast_bridge.mjs")
    monkeypatch.delenv("NODE_PATH", raising=False)
    capability = "postcss" if suffix == ".css" else "typescript"
    assert external.source_ast_bridge_capabilities(source)[capability]
    unrelated = tmp_path / f"unrelated{suffix}"
    unrelated.write_text(text)
    assert not external.source_ast_bridge_capabilities(unrelated)[capability]
    symbol = outline_source_file(source)[0]

    # When apply --check uses its ordinary OS temporary directory.
    payload = _check_apply_source_split(
        source,
        (SourceSplitMove(target, (symbol.name,)),),
        keep_compat_imports=True,
        force=False,
        package_relative=False,
        max_lines=500,
    )

    # Then parsing remains consistent, no project writes occur, and context is restored.
    assert payload["check"]["ok"]
    assert payload["result_outline"][1]["symbols"][0]["kind"] == symbol.kind
    assert source.read_text() == text
    assert not target.exists()
    assert not external.source_ast_bridge_capabilities(unrelated)[capability]

    # A failed plan must restore the parser context as well.
    with pytest.raises(ValueError, match="Symbol not found"):
        _check_apply_source_split(
            source,
            (SourceSplitMove(target, ("missing",)),),
            keep_compat_imports=True,
            force=False,
            package_relative=False,
            max_lines=500,
        )
    assert not external.source_ast_bridge_capabilities(unrelated)[capability]
