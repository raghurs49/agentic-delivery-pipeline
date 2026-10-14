def rolling_average(values, window):
    if window < 1:
        raise ValueError("window must be >= 1")
    return [round(sum(values[i:i + window]) / window, 2) for i in range(len(values) - window + 1)]
