def flatten_dict(d, sep="."):
    out = {}

    def walk(prefix, node):
        for k, v in node.items():
            key = f"{prefix}{sep}{k}" if prefix else str(k)
            if isinstance(v, dict):
                walk(key, v)
            else:
                out[key] = v

    walk("", d)
    return out
