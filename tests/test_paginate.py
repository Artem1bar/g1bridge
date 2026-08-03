from g1bridge.paginate import paginate, wrap_text


def test_wrap_respects_width():
    lines = wrap_text("a sentence that keeps going and going " * 5, max_chars=20)
    assert lines
    assert all(len(line) <= 20 for line in lines)


def test_wrap_keeps_word_boundaries():
    assert wrap_text("alpha beta gamma", max_chars=11) == ["alpha beta", "gamma"]


def test_wrap_hard_breaks_long_words():
    assert wrap_text("x" * 45, max_chars=20) == ["x" * 20, "x" * 20, "x" * 5]


def test_wrap_drops_blank_lines():
    assert wrap_text("one\n\n\ntwo", max_chars=10) == ["one", "two"]


def test_paginate_groups_lines_into_pages():
    text = "\n".join(f"line {i}" for i in range(12))
    pages = paginate(text, max_chars=10, lines_per_page=5)
    assert len(pages) == 3
    assert pages[0].count("\n") == 4  # 5 lines per full page
    assert pages[2] == "line 10\nline 11"


def test_paginate_empty_and_whitespace():
    assert paginate("") == []
    assert paginate("   \n  \n") == []


def test_paginate_single_short_page():
    assert paginate("hi there", max_chars=40, lines_per_page=5) == ["hi there"]
