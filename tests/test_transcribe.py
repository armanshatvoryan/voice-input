from voiceinput.transcribe import build_multipart, parse_response


def test_multipart_contains_fields_and_payload():
    body = build_multipart({"language": "auto"}, "speech.wav", b"RIFFdata", boundary="B")
    assert b'name="language"' in body
    assert b"auto" in body
    assert b'filename="speech.wav"' in body
    assert b"RIFFdata" in body
    assert body.endswith(b"--B--\r\n")


def test_multipart_payload_is_not_mangled():
    # audio is binary; any encoding step would corrupt it
    payload = bytes(range(256))
    body = build_multipart({}, "s.wav", payload, boundary="B")
    assert payload in body


def test_parse_json_text():
    assert parse_response(b'{"text": " hello"}') == " hello"


def test_parse_segments_fallback():
    body = b'{"segments": [{"text": "a "}, {"text": "b"}]}'
    assert parse_response(body) == "a b"


def test_parse_plain_text_body():
    assert parse_response(b"not json at all") == "not json at all"


def test_parse_unicode():
    assert parse_response('{"text": "Привет"}'.encode()) == "Привет"


def test_parse_unexpected_shape_is_empty():
    assert parse_response(b'{"error": "boom"}') == ""


# --- encoder context safety ---------------------------------------------------
# Measured on ggml-large-v3-turbo / M2. Reduced audio_ctx is opt-in because it
# fails silently: 576/640/704/832/896 returned garbage on a 5.8s clip that
# 512/768/1024/1280 transcribed perfectly, so no interpolating formula is safe.

import pytest

from voiceinput.transcribe import FULL_CONTEXT, SAFE_CONTEXTS, max_safe_duration


def test_full_context_covers_any_length():
    assert max_safe_duration(FULL_CONTEXT) == float("inf")


@pytest.mark.parametrize("duration,failing_ctx", [
    (9.7, 512),    # observed: "Codort banana." x3
    (10.4, 512),   # observed: "APRICAV" loop
    (16.0, 1024),  # observed: doubled sentence, lost the tail
])
def test_measured_failures_are_outside_the_safe_duration(duration, failing_ctx):
    assert duration > max_safe_duration(failing_ctx)


@pytest.mark.parametrize("duration,ctx", [
    (9.7, 768),    # observed MATCH at ~1.58x; our 1.8x rule promotes it to full
    (16.0, 1280),  # observed MATCH at ~1.60x; likewise
])
def test_rule_is_stricter_than_the_observed_boundary(duration, ctx):
    """Erring toward the full window is the safe direction; lock that in."""
    assert max_safe_duration(ctx) < duration


def test_short_dictation_fits_the_smallest_safe_context():
    assert max_safe_duration(512) >= 5.4  # longest clip measured MATCH at 512


def test_safe_duration_grows_with_context():
    durations = [max_safe_duration(c) for c in SAFE_CONTEXTS]
    assert durations == sorted(durations)


def test_safe_contexts_are_the_measured_ones():
    assert SAFE_CONTEXTS == (512, 768, 1024, 1280)


def test_transcribe_sends_audio_ctx():
    body = build_multipart({"audio_ctx": "512"}, "s.wav", b"x", boundary="B")
    assert b'name="audio_ctx"' in body and b"512" in body
