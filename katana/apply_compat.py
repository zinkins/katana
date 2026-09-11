from __future__ import annotations

import os
import re
from pathlib import Path

from .models import SourceSplitMove, SourceSymbol
from .outline_text import _typescript_doc_start
from .source_io import read_source_text
from .text_scan import BraceScanner

_TS_TYPE_KINDS = {"InterfaceDeclaration", "TypeAliasDeclaration", "interface", "type"}


def _text_leading_block(source: Path, lines: list[str]) -> list[str]:
    if source.suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        return _typescript_leading_block(lines)
    if source.suffix == ".dart":
        return _dart_leading_block(lines)
    if source.suffix == ".css":
        return _css_leading_block(lines)
    return []


def _typescript_leading_block(lines: list[str]) -> list[str]:
    end = 0
    open_braces = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if open_braces > 0:
            # Continuation of a multiline import statement.
            open_braces += line.count("{") - line.count("}")
            end = index + 1
            continue
        if not stripped:
            if end == index:
                end = index + 1
            continue
        if stripped in {'"use client"', '"use client";', "'use client'", "'use client';"}:
            end = index + 1
            continue
        if stripped.startswith(("import ", "import type ")):
            open_braces = line.count("{") - line.count("}")
            end = index + 1
            continue
        if end and (stripped.startswith(("}", "from "))):
            end = index + 1
            continue
        if stripped.startswith(("//", "/*", "*")):
            if end == index:
                end = index + 1
            continue
        end = min(end, _typescript_doc_start(lines, index + 1) - 1)
        break
    return lines[:end]


def _css_leading_block(lines: list[str]) -> list[str]:
    end = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(("@import", "@charset")):
            end = index + 1
            continue
        break
    return lines[:end]


def _dart_leading_block(lines: list[str]) -> list[str]:
    end = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if end == index:
                end = index + 1
            continue
        if stripped.startswith(("//", "/*", "*")) and end == index:
            end = index + 1
            continue
        if stripped.startswith(("library ", "import ", "export ", "part ")):
            end = index + 1
            continue
        break
    return lines[:end]


def _append_compat_imports(lines: list[str], insert: list[str]) -> list[str]:
    if not insert:
        return lines
    prefix = [] if not lines or not lines[-1].strip() else ["\n"]
    return [*lines, *prefix, *insert]


def _ts_test_registration_imports(
    source: Path,
    moves: tuple[SourceSplitMove, ...],
    symbols: list[SourceSymbol],
    terminator: str,
) -> list[str]:
    by_name = {symbol.name: symbol for symbol in symbols}
    registration_moves = [
        move
        for move in moves
        if any("test_registration" in by_name[name].tags for name in move.symbols)
    ]
    registration_moves.sort(
        key=lambda move: min(
            by_name[name].start_line
            for name in move.symbols
            if "test_registration" in by_name[name].tags
        )
    )
    return [
        f'import "{_js_relative_module_name(source, move.target)}"{terminator}\n'
        for move in registration_moves
    ]


def _apply_text_compat_exports(
    source: Path,
    lines: list[str],
    moves: tuple[SourceSplitMove, ...],
    symbols: list[SourceSymbol],
) -> list[str]:
    if source.suffix == ".dart":
        return _insert_after_text_leading_block(source, lines, _dart_compat_lines(source, moves))
    if source.suffix == ".css":
        return _insert_after_text_leading_block(source, lines, _css_compat_lines(source, moves))
    if source.suffix not in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        return lines
    moved = {name for move in moves for name in move.symbols}
    staying_refs = _text_staying_references(source, lines, moved, symbols)
    terminator = _ts_terminator(lines)
    imports = _ts_test_registration_imports(source, moves, symbols, terminator)
    exports: list[str] = []
    by_name = {symbol.name: symbol for symbol in symbols}
    for move in moves:
        module = _js_relative_module_name(source, move.target)
        # Re-exports do not bind names locally; staying code needs an import.
        needed = [name for name in move.symbols if name in staying_refs]
        if needed:
            imports.extend(_ts_named_lines("import", module, needed, by_name, terminator))
        # Compatibility keeps the old public surface; private helpers stay private.
        public = [name for name in move.symbols if _ts_was_exported(by_name.get(name))]
        if public:
            exports.extend(_ts_named_lines("export", module, public, by_name, terminator))
    lines = _insert_after_text_leading_block(source, lines, imports)
    return _append_compat_imports(lines, exports)


def _apply_text_usage_imports(
    source: Path,
    lines: list[str],
    moves: tuple[SourceSplitMove, ...],
    symbols: list[SourceSymbol],
) -> list[str]:
    if source.suffix not in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".dart"):
        return lines
    moved = {name for move in moves for name in move.symbols}
    staying_refs = _text_staying_references(source, lines, moved, symbols)
    imports: list[str] = []
    by_name = {symbol.name: symbol for symbol in symbols}
    if source.suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        imports.extend(_ts_test_registration_imports(source, moves, symbols, _ts_terminator(lines)))
    for move in moves:
        needed = sorted(set(move.symbols) & staying_refs)
        if not needed:
            continue
        if source.suffix == ".dart":
            imports.append(
                f"import '{_relative_uri(source, move.target)}' show {', '.join(needed)};\n"
            )
        else:
            imports.extend(
                _ts_named_lines(
                    "import",
                    _js_relative_module_name(source, move.target),
                    needed,
                    by_name,
                    _ts_terminator(lines),
                )
            )
    if not imports:
        return lines
    return _insert_after_text_leading_block(source, lines, imports)


def _text_staying_references(
    source: Path, lines: list[str], moved: set[str], symbols: list[SourceSymbol]
) -> set[str]:
    references = {
        reference
        for symbol in symbols
        if symbol.name not in moved
        for reference in symbol.references
        if reference in moved
    }
    if source.suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        # Outlines omit executable top-level statements, including test callbacks.
        # Scan retained code as well, excluding imports, comments and literal text.
        scanner = BraceScanner()
        for line in lines[len(_text_leading_block(source, lines)) :]:
            scanner.feed(line.rstrip("\r\n"))
        references.update(scanner.identifiers & moved)
    return references


def _ts_exported_block(block: list[str]) -> list[str]:
    if not block:
        return block
    from .models import _TS_SYMBOL_RE

    scanner = BraceScanner()
    for index, line in enumerate(block):
        stripped = line.lstrip()
        if scanner.in_code and _TS_SYMBOL_RE.match(stripped):
            if stripped.startswith("export "):
                return block
            indent = line[: len(line) - len(stripped)]
            return [*block[:index], f"{indent}export {stripped}", *block[index + 1 :]]
        scanner.feed(line)
    return block


def _ts_named_lines(
    verb: str,
    module: str,
    names: list[str],
    symbols: dict[str, SourceSymbol],
    terminator: str = "",
) -> list[str]:
    values, types = _ts_split_type_names(names, symbols)
    lines: list[str] = []
    if values:
        lines.append(f'{verb} {{ {", ".join(values)} }} from "{module}"{terminator}\n')
    if types:
        lines.append(f'{verb} type {{ {", ".join(types)} }} from "{module}"{terminator}\n')
    return lines


_TS_MODULE_STATEMENT_END_RE = re.compile(r"""^\s*import\s+["']|\bfrom\s+["']""")


def _ts_terminator(lines: list[str]) -> str:
    """Return ";" when the file's module statements (or, lacking any, its code) use one."""
    ends = [
        line.rstrip().endswith(";")
        for line in _typescript_leading_block(lines)
        if _TS_MODULE_STATEMENT_END_RE.search(line)
    ]
    if not ends:
        ends = [line.rstrip().endswith(";") for line in lines]
    return ";" if any(ends) else ""


def _ts_was_exported(symbol: SourceSymbol | None) -> bool:
    if symbol is None:
        return True
    meta = symbol.meta or {}
    if "exported" in meta:
        return bool(meta["exported"]) or bool(meta.get("default_export"))
    return symbol.signature.lstrip().startswith("export ")


def _ts_split_type_names(
    names: list[str],
    symbols: dict[str, SourceSymbol],
) -> tuple[list[str], list[str]]:
    values: list[str] = []
    types: list[str] = []
    for name in names:
        symbol = symbols.get(name)
        if symbol is not None and _ts_type_only_symbol(symbol):
            types.append(name)
        else:
            values.append(name)
    return values, types


def _ts_type_only_symbol(symbol: SourceSymbol) -> bool:
    meta = symbol.meta or {}
    return bool(meta.get("type_only")) or symbol.kind in _TS_TYPE_KINDS


def _dart_compat_lines(source: Path, moves: tuple[SourceSplitMove, ...]) -> list[str]:
    lines: list[str] = []
    for move in moves:
        # Library-private symbols cannot be shown across files; they only move
        # together with all their consumers.
        public = [name for name in move.symbols if not name.startswith("_")]
        if not public:
            continue
        module = _relative_uri(source, move.target)
        symbols = ", ".join(public)
        lines.append(f"import '{module}' show {symbols};\n")
        lines.append(f"export '{module}' show {symbols};\n")
    if lines:
        lines.append("\n")
    return lines


def _css_compat_lines(source: Path, moves: tuple[SourceSplitMove, ...]) -> list[str]:
    lines = [f'@import "{_relative_uri(source, move.target)}";\n' for move in moves]
    if lines:
        lines.append("\n")
    return lines


def _insert_after_text_leading_block(
    source: Path,
    lines: list[str],
    insert: list[str],
) -> list[str]:
    if not insert:
        return lines
    end = len(_text_leading_block(source, lines))
    suffix = ["\n"] if end < len(lines) and lines[end].strip() else []
    return [*lines[:end], *insert, *suffix, *lines[end:]]


def _js_relative_module_name(
    source: Path, target: Path, *, style_source: Path | None = None
) -> str:
    original = style_source or source
    text = read_source_text(original)
    extensions = re.findall(r"""(?:from|import)\s*["']\.{1,2}/[^"'\n]+(\.[cm]?[jt]sx?)["']""", text)
    suffix = extensions[0] if extensions else (source.suffix if source.suffix == ".mjs" else "")
    relative = os.path.relpath(target.with_suffix(suffix), source.parent).replace("\\", "/")
    return f"./{relative}" if not relative.startswith(".") else relative


def _typescript_target_block(source: Path, target: Path, block: list[str]) -> list[str]:
    """Copy directives and imports, rebased to the destination, without file docs."""
    kept: list[str] = []
    scanner = BraceScanner()
    copying = False
    for line in block:
        stripped = line.strip()
        if scanner.in_code and (
            stripped.startswith("import ")
            or stripped.rstrip(";") in {'"use client"', "'use client'"}
        ):
            copying = True
        if copying:
            kept.append(line)
        scanner.feed(line)
        if scanner.in_code and scanner.depth == 0:
            copying = False

    def rebase(match: re.Match[str]) -> str:
        module = source.parent / match["module"]
        relative = os.path.relpath(module, target.parent).replace("\\", "/")
        if not relative.startswith("."):
            relative = "./" + relative
        return match["prefix"] + match["quote"] + relative + match["quote"]

    pattern = (
        r"""(?P<prefix>\bfrom\s*|\bimport\s*)(?P<quote>["'])(?P<module>\.{1,2}/[^"']+)(?P=quote)"""
    )
    return re.sub(pattern, rebase, "".join(kept)).splitlines(keepends=True)


def _relative_uri(source: Path, target: Path) -> str:
    try:
        relative = target.relative_to(source.parent)
    except ValueError as error:
        raise ValueError("Split targets must be inside the source module directory") from error
    return "/".join(relative.parts)
