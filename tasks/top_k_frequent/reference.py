from collections import Counter


def top_k_frequent(words, k):
    if k < 1:
        raise ValueError("k must be >= 1")
    counts = Counter(words)
    return [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:k]
