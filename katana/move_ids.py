from __future__ import annotations

from pathlib import Path

from .models import SourceSplitMove


def resolve_move_ids(
    source: Path, moves: tuple[SourceSplitMove, ...]
) -> tuple[SourceSplitMove, ...]:
    """Accept fresh map IDs as well as legacy names, rejecting stale or foreign IDs."""
    if not any("::" in name for move in moves for name in move.symbols):
        return moves
    from .outline import analyze_source_file

    document = analyze_source_file(source)
    by_id = {symbol.id.split("::", 1)[1]: symbol.name for symbol in document.symbols}

    def resolve(value: str) -> str:
        if "::" not in value:
            return value
        path, identity = value.split("::", 1)
        if Path(path).resolve() != source.resolve() or identity not in by_id:
            raise ValueError(f"Stale or foreign symbol ID for {source}: {value}")
        return by_id[identity]

    return tuple(
        SourceSplitMove(move.target, tuple(resolve(n) for n in move.symbols)) for move in moves
    )
