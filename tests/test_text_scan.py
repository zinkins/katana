from katana.text_scan import brace_depths


def test_regex_after_return_does_not_hide_later_functions() -> None:
    # Given: a regular expression with an unmatched structural brace.
    lines = ["function first() {", "  return /{/;", "}", "function second() {}"]

    # When: the stateful scanner walks the module.
    depths = brace_depths(lines)

    # Then: the regex does not affect block nesting.
    assert depths == [1, 1, 0, 0]


def test_escaped_newline_keeps_a_quoted_string_open() -> None:
    # Given: a JavaScript string continued with a backslash.
    lines = ["function first() {", '  const s = "hello' + chr(92), '{world";', "}"]

    # When: the scanner consumes both physical lines of the string.
    depths = brace_depths(lines)

    # Then: braces in the continued string do not alter nesting.
    assert depths == [1, 1, 1, 0]


def test_division_remains_code_between_regular_expressions() -> None:
    # Given: division followed by a block on the same line.
    lines = ["function first() {", "  const half = size / 2; if (half) {", "  }", "}"]

    # When: the scanner distinguishes division from a regular expression.
    depths = brace_depths(lines)

    # Then: the real block is still counted.
    assert depths == [1, 2, 1, 0]
