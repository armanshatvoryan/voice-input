"""Post-processing of raw whisper output into pasteable text."""

from __future__ import annotations

import re

# whisper emits non-speech annotations on their own line: [BLANK_AUDIO], (music), [ Silence ]
_ANNOTATION = re.compile(r"^\s*[\[(][^\])]*[\])]\s*$")
_WS = re.compile(r"\s+")

# Hallucinated boilerplate whisper produces on silence / noise-only input.
_HALLUCINATIONS = {
    "thank you.",
    "thanks for watching!",
    "thank you for watching.",
    "thank you for watching!",
    "you",
    "субтитры сделал dimatorzok",
    "продолжение следует...",
    "редактор субтитров а.семкин корректор а.егорова",
}


def has_repetition_loop(text: str, max_phrase: int = 10) -> bool:
    """True if the text ends in the same phrase repeated back to back.

    This is whisper's signature failure when the encoder context is undersized: it
    does not error, it just stutters ("codeword banana. codeword banana. ..."). We
    treat it as a signal to redo the job with the full context window.
    """
    words = text.split()
    for size in range(2, max_phrase + 1):
        if len(words) < size * 2:
            break
        tail = words[-size:]
        if words[-2 * size:-size] == tail:
            return True
    return False


def clean(raw: str) -> str:
    """Strip whisper artifacts and normalise whitespace to a single paste-ready line."""
    if not raw:
        return ""
    kept = [line for line in raw.splitlines() if line.strip() and not _ANNOTATION.match(line)]
    text = _WS.sub(" ", " ".join(kept)).strip()
    if text.lower() in _HALLUCINATIONS:
        return ""
    return text
