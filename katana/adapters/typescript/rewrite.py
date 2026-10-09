from __future__ import annotations

import json
import os
import re
from pathlib import Path

from katana.models import SourceSplitMove, SourceSymbol
from katana.source_io import read_source_text


def is_commonjs(source: Path) -> bool:
    if source.suffix == ".cjs":
        return True
    if source.suffix not in (".js", ".jsx"):
        return False
    text = read_source_text(source)
    return bool(re.search(r"\bmodule\.exports\b|\bexports\.|\brequire\s*\(", text)) and not bool(
        re.search(r"(?m)^\s*(?:import\s|export\s)", text)
    )


def require_line(source: Path, target: Path, names: list[str]) -> str:
    module = os.path.relpath(target, source.parent).replace("\\", "/")
    if not module.startswith("."):
        module = "./" + module
    return f'const {{ {", ".join(names)} }} = require("{module}");\n'


def commonjs_usage_imports(
    source: Path, lines: list[str], moves: tuple[SourceSplitMove, ...], symbols: list[SourceSymbol]
) -> list[str]:
    from katana.apply_compat import _insert_after_text_leading_block, _text_staying_references

    moved = {name for move in moves for name in move.symbols}
    references = _text_staying_references(source, lines, moved, symbols)
    imports = [
        require_line(source, move.target, [name for name in move.symbols if name in references])
        for move in moves
        if references.intersection(move.symbols)
    ]
    return _insert_after_text_leading_block(source, lines, imports)


def validate_dependencies(
    source: Path, moves: tuple[SourceSplitMove, ...], symbols: list[SourceSymbol]
) -> None:
    by_name = {s.name: s for s in symbols}
    moved = {name for move in moves for name in move.symbols}
    commonjs = is_commonjs(source)
    locations = {name: move.target.resolve() for move in moves for name in move.symbols}
    for name in sorted(moved):
        symbol = by_name.get(name)
        if symbol is None:
            continue  # The shared planner reports unknown names.
        if "route_registration" in symbol.tags:
            raise ValueError(
                f"Cannot move {name}: route registrations must preserve execution order"
            )
        meta = symbol.meta or {}
        if commonjs and "start_byte" not in meta:
            raise ValueError(
                f"Cannot move {name}: CommonJS rewrite requires the TypeScript compiler AST"
            )
        for other in symbols:
            other_meta = other.meta or {}
            if (
                other.name != name
                and "start_byte" in meta
                and (meta["start_byte"], meta["end_byte"])
                == (other_meta.get("start_byte"), other_meta.get("end_byte"))
                and (locations.get(other.name) != locations[name])
            ):
                raise ValueError(
                    f"Cannot move {name}: shared declaration with {other.name} must move into one target"
                )
        if commonjs and meta.get("eager_after_line"):
            raise ValueError(
                f"Cannot move {name}: CommonJS initializer would change execution order"
            )
        if commonjs and any(call.get("dynamic") for call in meta.get("require_calls", ())):
            raise ValueError(f"Cannot move {name}: dynamic require cannot be rebased safely")
        unresolved = [
            ref
            for ref in symbol.references
            if ref not in moved
            and (
                commonjs
                or not (
                    (by_name[ref].meta or {}).get("exported")
                    or by_name[ref].signature.lstrip().startswith("export ")
                )
            )
        ]
        if unresolved:
            raise ValueError(
                f"Cannot move {name}: unresolved source-local dependencies: {', '.join(unresolved)}; "
                "move their dependency closure into the same target"
            )
    if commonjs:
        graph: dict[Path, set[Path]] = {target: set() for target in locations.values()}
        for name in moved & by_name.keys():
            graph[locations[name]].update(
                locations[ref]
                for ref in by_name[name].references
                if ref in locations and locations[ref] != locations[name]
            )
        for target in sorted(graph):
            pending = list(graph[target])
            visited: set[Path] = set()
            while pending:
                dependency = pending.pop()
                if dependency == target:
                    raise ValueError(
                        f"CommonJS dependency cycle involving {target}; keep the closure in one target"
                    )
                if dependency not in visited:
                    visited.add(dependency)
                    pending.extend(graph[dependency])


def rebase_requires(
    source: Path, target: Path, symbol: SourceSymbol, block: list[str]
) -> list[str]:
    if not block:
        return block
    meta = symbol.meta or {}
    data = "".join(block).encode("utf-8")
    start = int(meta.get("start_byte", 0))
    for call in reversed(meta.get("require_calls", ())):
        module = call.get("module", "")
        if not module.startswith("."):
            continue
        relative = os.path.relpath(source.parent / module, target.parent).replace("\\", "/")
        if not relative.startswith("."):
            relative = "./" + relative
        first, last = int(call["start_byte"]) - start, int(call["end_byte"]) - start
        data = data[:first] + json.dumps(relative).encode("utf-8") + data[last:]
    return data.decode("utf-8").splitlines(keepends=True)


def exact_blocks(
    text: str, symbols: list[SourceSymbol], moved: set[str]
) -> tuple[list[str], dict[str, list[str]]]:
    """Slice compiler byte spans; line spans are only used by structural analysis."""
    data = text.encode("utf-8")
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line.encode("utf-8")))
    ranges: list[tuple[int, int]] = []
    blocks: dict[str, list[str]] = {}
    for symbol in symbols:
        if symbol.name not in moved:
            continue
        meta = symbol.meta or {}
        start = int(meta.get("start_byte", offsets[symbol.start_line - 1]))
        end = int(meta.get("end_byte", offsets[symbol.end_line]))
        if (start, end) in ranges:
            blocks[symbol.name] = []
            continue
        if any(start < other_end and other_start < end for other_start, other_end in ranges):
            raise ValueError("Overlapping declarations cannot be moved independently")
        ranges.append((start, end))
        block = data[start:end].decode("utf-8")
        blocks[symbol.name] = (block if block.endswith("\n") else block + "\n").splitlines(
            keepends=True
        )
    kept = data
    for start, end in sorted(ranges, reverse=True):
        kept = kept[:start] + kept[end:]
    return kept.decode("utf-8").splitlines(keepends=True), blocks
