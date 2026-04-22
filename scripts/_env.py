"""Tiny .env loader — no extra dependencies.

Every verification / audit script in this directory imports this module at
the top, which auto-loads `<repo-root>/.env` into os.environ if present.
That way the user sets their API keys ONCE in .env and never has to
`export ANTHROPIC_API_KEY=...` again after opening a new terminal.

The .env file is git-ignored (.gitignore entry: `.env`) so keys never
land in the repo.  Format is the usual:

    # Comments start with #
    ANTHROPIC_API_KEY=sk-ant-api03-...
    GOOGLE_MAPS_API_KEY=AIzaSy...

Values are taken literally -- no shell interpolation, no quotes required.
If an env var is ALREADY set in the shell (e.g. user explicitly exported
it for this one run), the shell value wins -- .env never overwrites.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv() -> None:
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if not env_path.exists():
        return

    try:
        lines = env_path.read_text().splitlines()
    except Exception:
        return

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes if present (tolerated but not required)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        # Don't clobber values the user explicitly exported in their shell
        if key and key not in os.environ:
            os.environ[key] = value


# Auto-run on import — that's the whole point of this module
load_dotenv()
