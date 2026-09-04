"""Regressions in the structural (parser-free) TypeScript/JavaScript path.

These cover the fallback used when the TypeScript compiler API is missing:
top-level detection, comment ownership, human-readable `map` output, and the
module specifiers written into split targets.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from katana.cli import main
from katana.outline import outline_typescript_module
from katana.text_scan import brace_depths


@pytest.fixture(autouse=True)
def structural_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    # Select the fallback independently of installed Node packages.
    monkeypatch.setattr("katana.outline.outline_typescript_with_ast", lambda path: None)


MULTILINE_TEMPLATE = """\
function render(grouped) {
  const box = document.createElement('div');
  box.innerHTML = `
    <table>
      ${grouped ? `
        <tr><th>group</th></tr>
      ` : ''}
    </table>
  `;
  return box;
}

export function afterTemplate() {
  return 1;
}

export const TRAILING = 2;
"""


def test_brace_depth_survives_a_multiline_template_literal() -> None:
    # Given: a template literal that spans lines and interpolates with `${`.
    lines = MULTILINE_TEMPLATE.split("\n")

    # When: the brace tracker walks the file the way the outline does.
    depth = brace_depths(lines)[-1]

    # Then: the file closes at top level instead of staying open forever.
    assert depth == 0


def test_symbols_after_a_multiline_template_are_still_top_level(tmp_path: Path) -> None:
    # Given: a module whose first function embeds a multiline template literal.
    source = tmp_path / "render.js"
    source.write_text(MULTILINE_TEMPLATE, encoding="utf-8")

    # When: the structural outline lists its top-level symbols.
    names = [symbol.name for symbol in outline_typescript_module(source)]

    # Then: the declarations after the template are not swallowed by it.
    assert names == ["render", "afterTemplate", "TRAILING"]


def test_braces_inside_comments_do_not_open_a_block(tmp_path: Path) -> None:
    # Given: a comment that contains an unbalanced brace.
    source = tmp_path / "commented.js"
    source.write_text(
        "// opens a block { but never closes it\n"
        "export function first() {\n  return 1;\n}\n"
        "/* another stray { in a block comment */\n"
        "export function second() {\n  return 2;\n}\n",
        encoding="utf-8",
    )

    # When: the structural outline lists its top-level symbols.
    names = [symbol.name for symbol in outline_typescript_module(source)]

    # Then: both functions are seen at top level.
    assert names == ["first", "second"]


DOCUMENTED = """\
/** Doc for first. */
export function first() {
  return 1;
}

/** Doc for second. */
export function second() {
  return 2;
}
"""


def test_a_doc_comment_belongs_to_the_symbol_below_it(tmp_path: Path) -> None:
    # Given: two documented functions.
    source = tmp_path / "documented.js"
    source.write_text(DOCUMENTED, encoding="utf-8")

    # When: the structural outline reports their spans.
    symbols = {symbol.name: symbol for symbol in outline_typescript_module(source)}

    # Then: each span starts at its own doc comment and stops before the next one.
    assert symbols["first"].start_line == 1
    assert symbols["first"].end_line == 4
    assert symbols["second"].start_line == 6


def test_a_split_carries_each_doc_comment_with_its_symbol(tmp_path: Path) -> None:
    # Given: a documented module split symbol by symbol.
    source = tmp_path / "documented.js"
    source.write_text(DOCUMENTED, encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"moves": [{"target": "second.js", "symbols": ["second"]}]}),
        encoding="utf-8",
    )

    # When: the move is applied.
    main(["apply", str(source), "--plan-file", str(plan)])

    # Then: the doc travels with the symbol instead of staying behind.
    moved = (tmp_path / "second.js").read_text(encoding="utf-8")
    assert "Doc for second." in moved
    assert "Doc for first." not in moved
    assert "Doc for second." not in source.read_text(encoding="utf-8")


def test_map_prints_a_table_without_json(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # Given: any module katana can map.
    source = tmp_path / "documented.js"
    source.write_text(DOCUMENTED, encoding="utf-8")

    # When: the map is requested for a human reader.
    main(["map", str(source)])

    # Then: it renders the symbols instead of raising KeyError.
    out = capsys.readouterr().out
    assert "first" in out
    assert "second" in out


EXTENSIONED = """\
import { helper } from './helper.js';
import { shared } from '../shared/util.js';

export function moved() {
  return helper(shared);
}

export function stays() {
  return moved();
}
"""


def test_generated_specifiers_keep_the_extension_the_source_uses(tmp_path: Path) -> None:
    # Given: a module whose own relative imports carry the .js extension.
    source = tmp_path / "mod.js"
    source.write_text(EXTENSIONED, encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"moves": [{"target": "moved.js", "symbols": ["moved"]}]}),
        encoding="utf-8",
    )

    # When: the move is applied.
    main(["apply", str(source), "--plan-file", str(plan)])

    # Then: the import written back into the source resolves under plain ESM.
    kept = source.read_text(encoding="utf-8")
    assert "'./moved.js'" in kept or '"./moved.js"' in kept


def test_copied_imports_are_rebased_for_a_target_in_a_subdirectory(tmp_path: Path) -> None:
    # Given: a module that imports a sibling and a parent-directory module.
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "helper.js").write_text("export const helper = () => 1;\n", encoding="utf-8")
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "util.js").write_text("export const shared = 2;\n", encoding="utf-8")
    source = package / "mod.js"
    source.write_text(EXTENSIONED, encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"moves": [{"target": "parts/moved.js", "symbols": ["moved"]}]}),
        encoding="utf-8",
    )

    # When: the symbol moves one directory deeper.
    main(["apply", str(source), "--plan-file", str(plan)])

    # Then: its imports gain the level the new location needs.
    moved = (package / "parts" / "moved.js").read_text(encoding="utf-8")
    assert "'../helper.js'" in moved
    assert "'../../shared/util.js'" in moved


def test_a_target_does_not_inherit_the_source_module_header(tmp_path: Path) -> None:
    # Given: a module with a header comment describing the file as a whole.
    source = tmp_path / "mod.js"
    source.write_text(
        "/**\n * mod.js — the whole module.\n */\n\n" + DOCUMENTED,
        encoding="utf-8",
    )
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"moves": [{"target": "second.js", "symbols": ["second"]}]}),
        encoding="utf-8",
    )

    # When: one symbol is split out.
    main(["apply", str(source), "--plan-file", str(plan)])

    # Then: the header stays with the file it describes.
    moved = (tmp_path / "second.js").read_text(encoding="utf-8")
    assert "the whole module" not in moved
    assert "the whole module" in source.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "body",
    [
        "const view = `\nexport function fake() {}\n`;\n",
        "/*\nexport function fake() {}\n*/\n",
    ],
)
def test_declarations_inside_multiline_text_are_ignored(tmp_path: Path, body: str) -> None:
    # Given: declaration-shaped text inside a template or comment.
    source = tmp_path / "text.js"
    source.write_text(body + "export function real() {}\n", encoding="utf-8")

    # When: the structural outline scans the module.
    names = [symbol.name for symbol in outline_typescript_module(source)]

    # Then: only real declarations appear.
    assert names == (["view", "real"] if body.startswith("const") else ["real"])


def test_documented_private_symbol_is_exported_when_moved(tmp_path: Path) -> None:
    # Given: a non-exported function with its own documentation.
    source = tmp_path / "documented.js"
    source.write_text(
        DOCUMENTED.replace("export function second", "function second"), encoding="utf-8"
    )
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"moves": [{"target": "second.js", "symbols": ["second"]}]}), encoding="utf-8"
    )

    # When: the function moves to another module.
    main(["apply", str(source), "--plan-file", str(plan)])

    # Then: compatibility exports can resolve the documented function.
    moved = (tmp_path / "second.js").read_text(encoding="utf-8")
    assert "/** Doc for second. */\nexport function second" in moved


def test_child_after_multiline_template_is_visible(tmp_path: Path) -> None:
    # Given: two methods separated by a nested template interpolation.
    source = tmp_path / "view.js"
    source.write_text(
        "class View {\n"
        + MULTILINE_TEMPLATE.split("\n\n")[0].replace("function render", "render")
        + "\nafter() { return 1; }\n}\n",
        encoding="utf-8",
    )

    # When: the class outline reports its children.
    symbol = outline_typescript_module(source)[0]

    # Then: the trailing method remains a direct child.
    assert "after" in [child.name for child in symbol.children]


def test_constant_does_not_include_the_next_symbols_multiline_doc(tmp_path: Path) -> None:
    # Given: the constant/doc adjacency from the original reported file.
    source = tmp_path / "parse.js"
    source.write_text(
        "export const SUM_FRACTION_DIGITS = 6;\n\n"
        "/**\n * Units accepted by the parser.\n */\n"
        "export const UNIT_ALLOWLIST = new Set(['mm']);\n",
        encoding="utf-8",
    )

    # When: the fallback computes declaration spans.
    constant, units = outline_typescript_module(source)

    # Then: the single-line constant ends before the next declaration's doc.
    assert (constant.start_line, constant.end_line) == (1, 1)
    assert (units.start_line, units.end_line) == (3, 6)


@pytest.mark.parametrize("command", ["map", "outline"])
def test_plain_output_prints_reference_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    # Given: a module with a real reference between declarations.
    source = tmp_path / "refs.py"
    source.write_text(
        "def first():\n    return 1\n\ndef second():\n    return first()\n", encoding="utf-8"
    )

    # When: either public representation is printed as a table.
    main([command, str(source)])

    # Then: nested IR references and legacy string references are both readable.
    out = capsys.readouterr().out
    assert "refs=first" in out
    assert "first()" in out
    assert "second()" in out


def test_dependency_import_resolves_back_from_a_subdirectory(tmp_path: Path) -> None:
    # Given: a moved function uses a declaration that stays in the source.
    source = tmp_path / "mod.js"
    source.write_text(
        "import { helper } from './helper.js';\n"
        "export function moved() { return helper(shared()); }\n"
        "export function shared() { return 2; }\n",
        encoding="utf-8",
    )
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"moves": [{"target": "parts/moved.js", "symbols": ["moved"]}]}),
        encoding="utf-8",
    )

    # When: the function moves into a nested directory.
    main(["apply", str(source), "--plan-file", str(plan)])

    # Then: both generated and copied imports resolve relative to the target.
    moved = (tmp_path / "parts/moved.js").read_text(encoding="utf-8")
    assert 'from "../mod.js"' in moved
    assert "from '../helper.js'" in moved


def test_merge_rebases_imports_into_an_existing_subdirectory_target(tmp_path: Path) -> None:
    # Given: an existing target in a subdirectory and an extensioned source import.
    target = tmp_path / "parts" / "moved.js"
    target.parent.mkdir()
    target.write_text("export function existing() { return 0; }\n", encoding="utf-8")
    source = tmp_path / "mod.js"
    source.write_text(EXTENSIONED, encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"moves": [{"target": "parts/moved.js", "symbols": ["moved"]}]}),
        encoding="utf-8",
    )

    # When: the function merges into that target.
    main(["apply", str(source), "--plan-file", str(plan)])

    # Then: imports are rebased and existing declarations survive.
    moved = target.read_text(encoding="utf-8")
    assert "from '../helper.js'" in moved
    assert "function existing()" in moved
