"""py2app build spec for voice-input.app.

Why py2app rather than the earlier bash-wrapper .app: a wrapper that `exec`s the
Homebrew Python makes the *running* process identity `org.python.python`, so the
TCC (Accessibility / Microphone) grant on the bundle never applies. py2app ships a
signed bootstrap executable that loads libpython in-process — the running binary IS
`studio.arag.voice-input`, so grants actually stick.

Build:   .venv/bin/python setup_app.py py2app
Output:  dist/voice-input.app
"""

from setuptools import setup

APP = ["app_entry.py"]

OPTIONS = {
    "argv_emulation": False,   # Carbon-based; breaks menu-bar apps on Apple Silicon
    "packages": ["voiceinput", "rumps", "sounddevice", "pynput", "numpy"],
    # sounddevice dlopens PortAudio at runtime; ship the wheel's copy so the app
    # doesn't depend on a Homebrew install being present.
    "includes": ["_sounddevice_data"],
    "plist": {
        "CFBundleName": "voice-input",
        "CFBundleDisplayName": "Voice Input",
        "CFBundleIdentifier": "studio.arag.voice-input",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSUIElement": True,                  # menu-bar only, no Dock icon
        "LSMinimumSystemVersion": "12.0",
        "NSMicrophoneUsageDescription":
            "voice-input records your voice so it can be transcribed locally on this Mac.",
    },
}

setup(
    name="voice-input",
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
