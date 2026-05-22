from app.utils import chunk_text


def test_chunk_text_keeps_short_text_as_one_chunk() -> None:
    assert chunk_text("hello", limit=10) == ["hello"]


def test_chunk_text_splits_long_text() -> None:
    chunks = chunk_text("one two three four five", limit=9)
    assert len(chunks) > 1
    assert all(len(chunk) <= 9 for chunk in chunks)
