# Phase 2 – Step 2.2: load versioned prompt files (e.g. risk_analyst_V1.md) by name + version
"""Prompts live as Markdown files next to this module: `<name>_<version>.md`.

Changing PROMPT_VERSION in .env (e.g. V1 → V2) switches every agent to the new files, and the
version is stamped on each audit record, so we always know which prompt produced which result.
"""

import re
from pathlib import Path

from src import config

PROMPTS_DIR = Path(__file__).resolve().parent

# Phase 2 – Step 2.2: <!-- ... --> blocks are notes for developers, not instructions for the model
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


# Phase 2 – Step 2.2: read a prompt file and strip developer comments
def load_prompt(name: str, version: str | None = None) -> str:
    """Return the text of `<name>_<version>.md` (version defaults to config.PROMPT_VERSION)."""
    version = version or config.PROMPT_VERSION
    path = PROMPTS_DIR / f"{name}_{version}.md"
    if not path.exists():
        available = sorted(p.name for p in PROMPTS_DIR.glob(f"{name}_*.md"))
        raise FileNotFoundError(f"Prompt {path.name} not found. Available: {available}")
    return _HTML_COMMENT.sub("", path.read_text(encoding="utf-8")).strip()
