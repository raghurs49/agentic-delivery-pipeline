import re

UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text):
    s = text.strip()
    if not s:
        raise ValueError("empty duration")
    pos, seen, total = 0, set(), 0
    for m in re.finditer(r"\s*(\d+)\s*([a-zA-Z])\s*", s):
        if m.start() != pos:
            raise ValueError("bad duration")
        unit = m.group(2)
        if unit not in UNITS or unit in seen:
            raise ValueError("bad unit")
        seen.add(unit)
        total += int(m.group(1)) * UNITS[unit]
        pos = m.end()
    if pos != len(s):
        raise ValueError("bad duration")
    return total
