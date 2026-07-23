"""Optional second pass: run the transcript through the Claude CLI to tidy it up."""

from __future__ import annotations

import subprocess


def polish(text: str, cfg: dict) -> tuple[str, str | None]:
    """Return (text, error). On any failure the raw transcript is returned unchanged.

    The transcript goes in over stdin, never in argv — it is untrusted model-adjacent
    input and must not be able to shape the command line.
    """
    if not text.strip():
        return text, None
    cleanup = cfg["cleanup"]
    try:
        result = subprocess.run(
            [cleanup["claude_bin"], "-p", cleanup["prompt"]],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=cleanup["timeout_s"],
        )
    except FileNotFoundError:
        return text, f"{cleanup['claude_bin']} not found on PATH"
    except subprocess.TimeoutExpired:
        return text, f"cleanup timed out after {cleanup['timeout_s']}s"

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[:200]
        return text, f"claude exited {result.returncode}: {detail}"

    polished = result.stdout.decode("utf-8", "replace").strip()
    if not polished:
        return text, "claude returned empty output"
    return polished, None
