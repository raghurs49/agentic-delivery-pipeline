VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(s):
    if not s or any(c not in VALUES for c in s):
        raise ValueError("invalid roman numeral")
    total = 0
    for i, c in enumerate(s):
        v = VALUES[c]
        if i + 1 < len(s) and VALUES[s[i + 1]] > v:
            total -= v
        else:
            total += v
    return total
