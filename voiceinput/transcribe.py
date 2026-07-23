"""Client for the local whisper.cpp HTTP server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BOUNDARY = "----voiceinput7MA4YWxkTrZu0gW"

FRAMES_PER_SECOND = 50   # whisper encoder frames; the full 1500 window covers 30s
FULL_CONTEXT = 0         # whisper-server's "use the whole window"

# Shrinking audio_ctx roughly halves latency, and we measured it as unsafe: on an
# M2 / large-v3-turbo, 576, 640, 704, 832 and 896 all returned garbage or looped on
# clips that 512, 768 and 1024 transcribed perfectly. The damage is silent — wrong
# words, not an error — so the default is the full window and any reduced value is
# opt-in per config. See README "Why audio_ctx is off by default".
SAFE_CONTEXTS = (512, 768, 1024, 1280)


def max_safe_duration(audio_ctx: int, headroom: float = 1.8) -> float:
    """Longest clip a given reduced context can cover before it starts looping."""
    if audio_ctx == FULL_CONTEXT:
        return float("inf")
    return audio_ctx / (FRAMES_PER_SECOND * headroom)


def build_multipart(fields: dict[str, str], filename: str, payload: bytes,
                    boundary: str = BOUNDARY) -> bytes:
    """Encode a multipart/form-data body. Deterministic boundary keeps this testable."""
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n".encode()
    )
    parts.append(payload)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts)


def parse_response(body: bytes) -> str:
    """whisper-server returns {"text": ...}; fall back to raw text if it ever changes."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body.decode("utf-8", "replace")
    if isinstance(data, dict):
        if isinstance(data.get("text"), str):
            return data["text"]
        segments = data.get("segments")
        if isinstance(segments, list):
            return "".join(s.get("text", "") for s in segments if isinstance(s, dict))
    return ""


def transcribe(wav_bytes: bytes, url: str, language: str = "auto", timeout: int = 120,
               audio_ctx: int = FULL_CONTEXT) -> str:
    body = build_multipart(
        {
            "temperature": "0.0",
            "response_format": "json",
            "language": language,
            "no_timestamps": "true",
            "audio_ctx": str(audio_ctx),
        },
        "speech.wav",
        wav_bytes,
    )
    request = urllib.request.Request(
        f"{url}/inference",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return parse_response(response.read())
