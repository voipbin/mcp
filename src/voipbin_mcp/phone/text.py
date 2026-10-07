"""Text helpers for live phone conversations.

``split_text`` cuts agent speech into pieces the speaking API accepts (the
server limit is 5000 bytes of UTF-8, measured with Go ``len``, not characters).
``estimate_seconds`` guesses how long a piece takes to play, because the
platform publishes no customer-visible "speech finished" event.
"""

from __future__ import annotations

import re

# bin-tts-manager/pkg/speakinghandler/speaking.go:187-188 (Go len => bytes).
MAX_SAY_BYTES = 5000

# Playback estimate: Latin-like text ~15 chars/s, CJK ~6 chars/s, at least one
# second, plus a fixed one second of synthesis/playout latency.
LATIN_CHARS_PER_SECOND = 15.0
CJK_CHARS_PER_SECOND = 6.0
MIN_SPEECH_SECONDS = 1.0
FIXED_LATENCY_SECONDS = 1.0

# A sentence ends at one of these marks, optionally followed by closing quotes
# or brackets, and then whitespace or the end of the text. A newline is also a
# boundary.
_SENTENCE_END = re.compile(r"[.!?\u3002\uff01\uff1f\u2026][\"'\u201d\u2019)\]]*(?:\s+|$)|\n+")


def is_cjk(ch: str) -> bool:
    """True for Hangul, CJK ideographs, and Japanese kana."""
    code = ord(ch)
    return (
        0x1100 <= code <= 0x11FF  # Hangul Jamo
        or 0x3040 <= code <= 0x30FF  # Hiragana, Katakana
        or 0x3130 <= code <= 0x318F  # Hangul compatibility Jamo
        or 0x3400 <= code <= 0x4DBF  # CJK extension A
        or 0x4E00 <= code <= 0x9FFF  # CJK unified ideographs
        or 0xAC00 <= code <= 0xD7AF  # Hangul syllables
        or 0xF900 <= code <= 0xFAFF  # CJK compatibility ideographs
    )


def estimate_seconds(text: str) -> float:
    """Estimated playback time of ``text``, including the fixed latency."""
    cjk = sum(1 for ch in text if is_cjk(ch))
    other = len(text) - cjk
    speech = cjk / CJK_CHARS_PER_SECOND + other / LATIN_CHARS_PER_SECOND
    return max(MIN_SPEECH_SECONDS, speech) + FIXED_LATENCY_SECONDS


def _prefix_within(text: str, max_bytes: int) -> int:
    """Number of leading characters of ``text`` whose UTF-8 fits ``max_bytes``."""
    used = 0
    for index, ch in enumerate(text):
        size = len(ch.encode("utf-8"))
        if used + size > max_bytes:
            return index
        used += size
    return len(text)


def split_text(text: str, max_bytes: int = MAX_SAY_BYTES) -> list[str]:
    """Split ``text`` into pieces of at most ``max_bytes`` UTF-8 bytes.

    A piece ends at the last sentence boundary that fits; when no boundary
    fits, it is cut at the largest character boundary that fits, so a
    multi-byte character is never split. Empty pieces are dropped.
    """
    if max_bytes < 4:
        raise ValueError("max_bytes must allow at least one UTF-8 character")
    pieces: list[str] = []
    rest = text.strip()
    while rest:
        if len(rest.encode("utf-8")) <= max_bytes:
            pieces.append(rest)
            break
        limit = _prefix_within(rest, max_bytes)
        window = rest[:limit]
        cut = 0
        for match in _SENTENCE_END.finditer(window):
            cut = match.end()
        if cut <= 0:
            cut = limit
        piece = rest[:cut].strip()
        if piece:
            pieces.append(piece)
        rest = rest[cut:].strip()
    return pieces
