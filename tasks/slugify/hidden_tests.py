import pytest
from solution import slugify


@pytest.mark.parametrize("text,slug", [
    ("Hello, World!", "hello-world"),
    ("  Multiple   spaces  ", "multiple-spaces"),
    ("Already-slug", "already-slug"),
    ("C++ & C#", "c-c"),
    ("Python 3.12", "python-3-12"),
    ("caf\u00e9 au lait", "caf-au-lait"),
    ("---x---", "x"),
])
def test_slugs(text, slug):
    assert slugify(text) == slug


@pytest.mark.parametrize("text", ["", "!!!", "   ", "\u00e9\u00e8"])
def test_empty_results(text):
    assert slugify(text) == ""
