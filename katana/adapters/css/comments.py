from __future__ import annotations

import re
from dataclasses import replace

from katana.models import SourceSymbol


def attach_leading_comments(symbols: list[SourceSymbol], lines: list[str]) -> list[SourceSymbol]:
    """Move standalone explanatory comments with the following top-level rule."""
    result: list[SourceSymbol] = []
    previous_end = 0
    for symbol in symbols:
        gap = "\n".join(lines[previous_end : symbol.start_line - 1])
        boundary = len(gap)
        start = symbol.start_line
        for comment in reversed(list(re.finditer(r"/\*[\s\S]*?\*/", gap))):
            if gap[comment.end() : boundary].strip():
                break
            line_start = gap.rfind("\n", 0, comment.start()) + 1
            if gap[line_start : comment.start()].strip():
                break
            start = previous_end + gap.count("\n", 0, comment.start()) + 1
            boundary = comment.start()
        result.append(replace(symbol, start_line=start))
        previous_end = symbol.end_line
    return result
