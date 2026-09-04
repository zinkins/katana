"""Brace nesting for the parser-free TypeScript/JavaScript path.

The structural outline decides what is top level by counting braces. Counting
them one line at a time is wrong the moment a string, a template literal or a
block comment spans lines: a template that interpolates with `${` leaves an
unmatched brace behind, every later declaration looks nested, and the split
silently cuts the file in the wrong place. This scanner carries that state
across lines instead.
"""

from __future__ import annotations

# A `/` starts a regular expression only where a value cannot appear yet.
# Anything else is division, whose operands carry no braces.
_REGEX_ALLOWED_BEFORE = frozenset("(,=:[!&|?{};+-*%~^<>")
_REGEX_ALLOWED_AFTER_WORD = {
    "return",
    "throw",
    "case",
    "delete",
    "void",
    "typeof",
    "yield",
    "await",
}


class BraceScanner:
    """Tracks brace depth across the lines of one file."""

    def __init__(self) -> None:
        self.depth = 0
        # Innermost last: "code" counts braces, "template" does not.
        self._contexts: list[str] = ["code"]
        self._depths: list[int] = [0]
        self._block_comment = False
        self._previous = ""
        self._quote = ""

    @property
    def in_code(self) -> bool:
        return self._contexts == ["code"] and not self._block_comment and not self._quote

    def feed(self, line: str) -> int:
        """Consume one line and return the resulting top-level depth."""
        index = 0
        while index < len(line):
            if self._quote:
                index = self._skip_string(line, index, self._quote, continuation=True)
            elif self._block_comment:
                index = self._feed_block_comment(line, index)
            elif self._contexts[-1] == "template":
                index = self._feed_template(line, index)
            else:
                index = self._feed_code(line, index)
                if index < 0:
                    break
        self.depth = self._depths[0]
        return self.depth

    def _feed_block_comment(self, line: str, index: int) -> int:
        if line[index] == "*" and line[index + 1 : index + 2] == "/":
            self._block_comment = False
            return index + 2
        return index + 1

    def _feed_template(self, line: str, index: int) -> int:
        char = line[index]
        if char == "\\":
            return index + 2
        if char == "`":
            self._pop_context()
            self._previous = "`"
            return index + 1
        if char == "$" and line[index + 1 : index + 2] == "{":
            self._contexts.append("code")
            self._depths.append(0)
            self._previous = "{"
            return index + 2
        return index + 1

    def _feed_code(self, line: str, index: int) -> int:
        """Return the next index, or -1 when the rest of the line is a comment."""
        char = line[index]
        if char.isspace():
            return index + 1
        if char == "/" and line[index + 1 : index + 2] == "/":
            return -1
        if char == "/" and line[index + 1 : index + 2] == "*":
            self._block_comment = True
            return index + 2
        if char.isalpha() or char in "_$":
            end = index + 1
            while end < len(line) and (line[end].isalnum() or line[end] in "_$"):
                end += 1
            self._previous = line[index:end]
            return end
        if char == "/" and (
            self._previous == ""
            or self._previous in _REGEX_ALLOWED_BEFORE
            or self._previous in _REGEX_ALLOWED_AFTER_WORD
        ):
            self._previous = "/"
            return self._skip_regex(line, index)
        if char in "'\"":
            self._previous = char
            return self._skip_string(line, index, char)
        if char == "`":
            self._contexts.append("template")
            self._depths.append(0)
            self._previous = "`"
            return index + 1
        if char == "{":
            self._depths[-1] += 1
        elif char == "}":
            if self._depths[-1] == 0 and len(self._contexts) > 1:
                # Closing brace of a `${...}` interpolation.
                self._pop_context()
            else:
                self._depths[-1] -= 1
        self._previous = char
        return index + 1

    def _pop_context(self) -> None:
        self._contexts.pop()
        self._depths.pop()

    def _skip_string(self, line: str, index: int, quote: str, *, continuation: bool = False) -> int:
        self._quote = ""
        if not continuation:
            index += 1
        while index < len(line):
            char = line[index]
            if char == "\\":
                if index + 1 == len(line):
                    self._quote = quote
                index += 2
                continue
            if char == quote:
                return index + 1
            index += 1
        # An unterminated quote ends at the newline: JavaScript forbids it to
        # continue, so the next line starts clean.
        return index

    @staticmethod
    def _skip_regex(line: str, index: int) -> int:
        index += 1
        in_class = False
        while index < len(line):
            char = line[index]
            if char == "\\":
                index += 2
                continue
            if char == "[":
                in_class = True
            elif char == "]":
                in_class = False
            elif char == "/" and not in_class:
                return index + 1
            index += 1
        return index


def brace_depths(lines: list[str]) -> list[int]:
    """Depth after each line, with multiline constructs accounted for."""
    scanner = BraceScanner()
    return [scanner.feed(line) for line in lines]
