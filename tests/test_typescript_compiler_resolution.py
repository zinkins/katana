from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from katana import SourceSplitMove, external, outline_source_file
from katana.cli_output import _check_apply_source_split


def _package(root: Path, body: str) -> None:
    package = root / "node_modules" / "typescript"
    package.mkdir(parents=True)
    (package / "index.js").write_text(body, encoding="utf-8")


@pytest.fixture
def compiler_path() -> str:
    if shutil.which("node") is None:
        pytest.skip("Node is required")
    resolved = subprocess.run(
        ["node", "-p", "require.resolve('typescript')"],
        capture_output=True,
        text=True,
        check=False,
    )
    if resolved.returncode:
        pytest.skip("Optional TypeScript dependency is unavailable")
    return resolved.stdout.strip()


def _selected_version(source: Path, bridge: Path) -> str:
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "const { loadOptional } = await import(process.argv[1]); "
                "console.log(loadOptional('typescript', process.argv[2])?.version);"
            ),
            (bridge / "ast_bridge_modules.mjs").as_uri(),
            str(source),
        ],
        cwd=source.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def isolated_bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    if shutil.which("node") is None:
        pytest.skip("Node is required")
    project = tmp_path / "project"
    project.mkdir()
    (project / "package.json").write_text("{}", encoding="utf-8")
    source = project / "source.cjs"
    source.write_text(
        "function helper() { return 42; }\nmodule.exports = helper;\n", encoding="utf-8"
    )
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    for file in external._NODE_BRIDGE.parent.glob("*.mjs"):
        shutil.copy2(file, bridge / file.name)
    monkeypatch.setattr(external, "_NODE_BRIDGE", bridge / "ast_bridge.mjs")
    monkeypatch.delenv("NODE_PATH", raising=False)
    return source, bridge


@pytest.mark.parametrize(
    "broken",
    [
        "module.exports = { version: '7.0.2' };",
        "throw new Error('broken TypeScript package');",
    ],
)
def test_compiler_api_falls_back_to_own_typescript(
    isolated_bridge: tuple[Path, Path], compiler_path: str, broken: str
) -> None:
    source, bridge = isolated_bridge
    _package(source.parent, broken)
    _package(bridge, f"module.exports = require({json.dumps(compiler_path)});")
    assert _selected_version(source, bridge) == "5.9.3"
    assert external.typescript_ast_available(source)
    assert external.source_ast_bridge_capabilities(source)["typescript"]
    assert outline_source_file(source)[0].kind == "FunctionDeclaration"
    result = _check_apply_source_split(
        source,
        (SourceSplitMove(source.with_name("helper.cjs"), ("helper",)),),
        keep_compat_imports=True,
        force=False,
        package_relative=False,
        max_lines=500,
    )
    assert result["check"]["ok"]
    assert not source.with_name("helper.cjs").exists()


def test_compatible_project_compiler_keeps_priority(
    isolated_bridge: tuple[Path, Path], compiler_path: str
) -> None:
    source, bridge = isolated_bridge
    _package(
        source.parent,
        f"module.exports = {{ ...require({json.dumps(compiler_path)}), version: 'compatible-project' }};",
    )
    _package(bridge, f"module.exports = require({json.dumps(compiler_path)});")
    assert _selected_version(source, bridge) == "compatible-project"


@pytest.mark.parametrize(
    "body",
    [
        "module.exports = { version: '7.0.2' };",
        "module.exports = { createSourceFile() {}, ScriptTarget: {} };",
        "throw new Error('broken TypeScript package');",
    ],
)
def test_package_without_compiler_api_is_unavailable(
    isolated_bridge: tuple[Path, Path], body: str
) -> None:
    source, _ = isolated_bridge
    _package(source.parent, body)
    assert not external.typescript_ast_available(source)
    capabilities = external.source_ast_bridge_capabilities(source)
    assert capabilities["node"]
    assert not capabilities["typescript"]
