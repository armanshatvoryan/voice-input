"""py2app entry point. Kept as a plain script (not a module) because py2app builds
from a script path; it just hands off to the menu-bar app."""

from voiceinput.menubar import main

if __name__ == "__main__":
    main()
