import pytest
from solution import parse_duration


@pytest.mark.parametrize("text,secs", [("1h30m", 5400), ("45s", 45), ("2d4h", 187200), ("1h 15m 30s", 4530), ("30m1h", 5400), ("0s", 0), ("1d", 86400)])
def test_valid(text, secs):
    assert parse_duration(text) == secs


@pytest.mark.parametrize("bad", ["", "   ", "h", "10", "5x", "1h2h", "1h 30", "m5"])
def test_invalid(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)
