def chunk_text(text, size, overlap=0):
    if size < 1 or overlap < 0 or overlap >= size:
        raise ValueError("invalid size/overlap")
    chunks, i = [], 0
    while i < len(text):
        chunks.append(text[i:i + size])
        if i + size >= len(text):
            break
        i += size - overlap
    return chunks
