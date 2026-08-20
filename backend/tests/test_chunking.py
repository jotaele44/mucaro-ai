from backend.app.chunker import chunk_text


def test_chunk_text_stable_shape():
    chunks = chunk_text("src_deadbeef", "alpha beta gamma " * 200, chunk_size=100, overlap=10)
    assert chunks
    assert chunks[0]["chunk_id"].startswith("chunk_")
    assert chunks[0]["source_id"] == "src_deadbeef"
    assert chunks[0]["char_start"] == 0
