PAIRS = {")": "(", "]": "[", "}": "{"}


def is_balanced(text):
    stack = []
    for c in text:
        if c in "([{":
            stack.append(c)
        elif c in PAIRS:
            if not stack or stack.pop() != PAIRS[c]:
                return False
    return not stack
