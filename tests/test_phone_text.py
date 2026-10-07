"""Unit tests for voipbin_mcp.phone.text (say splitting and speech estimates)."""

import pytest

from voipbin_mcp.phone.text import MAX_SAY_BYTES, estimate_seconds, split_text


def _bytes(piece: str) -> int:
    return len(piece.encode("utf-8"))


class TestSplitText:
    def test_short_text_is_one_piece(self):
        assert split_text("Hello there.") == ["Hello there."]

    def test_empty_and_blank_text_give_no_piece(self):
        assert split_text("") == []
        assert split_text("   \n ") == []

    def test_default_limit_is_the_server_byte_limit(self):
        # bin-tts-manager checks Go len(), i.e. UTF-8 bytes, not characters.
        assert MAX_SAY_BYTES == 5000

    def test_korean_multibyte_boundary_never_splits_a_character(self):
        # 3 bytes per syllable: 2000 syllables = 6000 bytes, no sentence marks.
        text = "가" * 2000
        pieces = split_text(text)
        assert len(pieces) == 2
        assert all(_bytes(p) <= MAX_SAY_BYTES for p in pieces)
        assert "".join(pieces) == text
        # 5000 // 3 = 1666 whole syllables fit in the first piece.
        assert len(pieces[0]) == 1666

    def test_mixed_width_characters_respect_the_byte_limit(self):
        text = ("a가😀" * 900)  # 1 + 3 + 4 bytes per triple
        pieces = split_text(text, max_bytes=1000)
        assert all(_bytes(p) <= 1000 for p in pieces)
        assert "".join(pieces) == text

    def test_sentence_boundary_is_preferred(self):
        first = "This is the first sentence. "
        second = "Second one is here and it is long enough to overflow."
        pieces = split_text(first + second, max_bytes=60)
        assert pieces[0] == "This is the first sentence."
        assert all(_bytes(p) <= 60 for p in pieces)

    def test_korean_sentence_boundary_is_preferred(self):
        text = "안녕하세요. 반갑습니다. " + "가" * 30
        pieces = split_text(text, max_bytes=60)
        assert pieces[0] in ("안녕하세요. 반갑습니다.", "안녕하세요.")
        assert all(_bytes(p) <= 60 for p in pieces)
        assert "".join(pieces).replace(" ", "") == text.replace(" ", "")

    def test_last_boundary_inside_the_window_is_used(self):
        text = "One. Two. Three. " + "x" * 40
        pieces = split_text(text, max_bytes=30)
        assert pieces[0] == "One. Two. Three."

    def test_newline_is_a_boundary(self):
        pieces = split_text("first line\nsecond line that is longer", max_bytes=20)
        assert pieces[0] == "first line"

    def test_tiny_limit_is_refused(self):
        with pytest.raises(ValueError):
            split_text("abc", max_bytes=3)


class TestEstimate:
    def test_minimum_is_one_second_plus_latency(self):
        assert estimate_seconds("Hi") == pytest.approx(2.0)
        assert estimate_seconds("") == pytest.approx(2.0)

    def test_latin_rate_is_fifteen_chars_per_second(self):
        assert estimate_seconds("a" * 150) == pytest.approx(10.0 + 1.0)

    def test_cjk_rate_is_six_chars_per_second(self):
        assert estimate_seconds("가" * 60) == pytest.approx(10.0 + 1.0)
        assert estimate_seconds("漢" * 30) == pytest.approx(5.0 + 1.0)
        assert estimate_seconds("あ" * 12) == pytest.approx(2.0 + 1.0)

    def test_mixed_text_adds_both_rates(self):
        assert estimate_seconds("a" * 30 + "가" * 12) == pytest.approx(2.0 + 2.0 + 1.0)
