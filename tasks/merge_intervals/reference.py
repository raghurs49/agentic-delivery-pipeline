def merge_intervals(intervals):
    out = []
    for s, e in sorted((list(i) for i in intervals), key=lambda x: x[0]):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out
