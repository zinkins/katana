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
    source_safe_move_groups,
)

_PROBE = Path(__file__).with_suffix(".mjs")
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None
    or source_parser_capabilities(_PROBE)["parser"] != "typescript_compiler_ast",
    reason="TypeScript compiler bridge is required",
)

# An Electron-style entry: a timer reassigned by startup code, a client created
# after an ordering-sensitive call, and one helper that only calls a method.
_ENTRY = (
    'setName("app");\n'
    "let timer = null;\n"
    "const client = createClient();\n"
    "function stop() {\n  clearInterval(timer);\n  timer = null;\n}\n"
    'function describeEmitter(emitter) {\n  emitter.stop();\n  return "emitter";\n}\n'
    "if (ready()) {\n  timer = setInterval(stop, 10);\n}\n"
)


def test_module_state_pins_reassigned_and_late_initialized_bindings(tmp_path: Path) -> None:
    # Given an entry whose startup code reassigns a binding after an ordered call.
    source = tmp_path / "main.mjs"
    source.write_text(_ENTRY, encoding="utf-8")

    # When Katana maps the module and lists safe move groups.
    symbols = outline_source_file(source)
    constraints = {
        symbol.name: symbol.constraints for symbol in analyze_source_file(source).symbols
    }
    groups = source_safe_move_groups(source, symbols)["groups"]
    references = {symbol.name: symbol.references for symbol in symbols}

    # Then state stays in the entry and only the independent helper is offered.
    assert constraints["timer"].movability == "pinned"
    assert constraints["timer"].pinned_reason == "reassigned by top-level statements"
    assert constraints["timer"].required_with == ("stop",)
    assert constraints["client"].movability == "pinned"
    assert "line 1" in (constraints["client"].pinned_reason or "")
    assert [group["symbols"] for group in groups] == [["describeEmitter"]]
    assert references["describeEmitter"] == ()
    assert references["stop"] == ("timer",)


def test_module_state_rejects_plans_that_split_a_binding_from_its_writers(
    tmp_path: Path,
) -> None:
    # Given the same entry.
    source = tmp_path / "main.mjs"
    source.write_text(_ENTRY, encoding="utf-8")

    # When a plan moves a binding that startup code reassigns, it is rejected.
    with pytest.raises(ValueError, match="reassigned by top-level statements"):
        plan_source_split(source, (SourceSplitMove(tmp_path / "state.mjs", ("timer",)),))

    # Then a plan that separates a writer from its binding is rejected too.
    with pytest.raises(ValueError, match="stop reassigns timer"):
        plan_source_split(source, (SourceSplitMove(tmp_path / "stop.mjs", ("stop",)),))


def test_module_state_warns_when_an_initializer_would_run_earlier(tmp_path: Path) -> None:
    # Given the same entry.
    source = tmp_path / "main.mjs"
    source.write_text(_ENTRY, encoding="utf-8")

    # When an explicit plan moves the client created after setName.
    plan = plan_source_split(source, (SourceSplitMove(tmp_path / "client.mjs", ("client",)),))

    # Then the plan warns that the initializer now runs before that statement.
    assert (
        "client initializer will run before the top-level statement at line 1; "
        "verify initialization order"
    ) in plan.warnings


def test_module_state_moves_a_binding_together_with_its_writer(tmp_path: Path) -> None:
    # Given a counter whose binding is only reassigned by a function.
    source = tmp_path / "counter.mjs"
    source.write_text(
        "let count = 0;\n"
        "function increment() {\n  count += 1;\n}\n"
        "function read() {\n  return count;\n}\n"
        "increment();\n"
        "console.log(read());\n",
        encoding="utf-8",
    )
    groups = source_safe_move_groups(source, outline_source_file(source))["groups"]

    # When the binding, its writer and its reader move into one module.
    apply_source_split(
        source,
        (SourceSplitMove(tmp_path / "state.mjs", ("count", "increment", "read")),),
    )
    result = subprocess.run(
        ["node", str(source)],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then every safe group keeps the writer with the binding and runtime is unchanged.
    assert all("increment" in group["symbols"] for group in groups if "count" in group["symbols"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"
