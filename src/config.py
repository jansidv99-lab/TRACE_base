# Phase 1 – Step 1.1: single place that reads settings from .env / environment variables
"""Project settings.

Values come from environment variables, with `.env` (repo root) loaded first if it exists.
Real environment variables win over `.env`, which is what Cloud Run will rely on in Stage 06.
Missing LLM settings are allowed here: nothing in Stage 01 calls the LLM.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Phase 1 – Step 1.1: repo root = parent of the `src/` folder, so paths work from any cwd
PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env", override=False)


# Phase 1 – Step 1.1: resolve a path setting relative to the repo root unless it is absolute
def _path_setting(name: str, default: str) -> Path:
    path = Path(os.getenv(name, default))
    return path if path.is_absolute() else PROJECT_ROOT / path


DATA_DIR: Path = _path_setting("DATA_DIR", "project_data")
LOG_DIR: Path = _path_setting("LOG_DIR", "logs")

OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL: str | None = os.getenv("OPENAI_BASE_URL")
MODEL: str | None = os.getenv("MODEL")
JUDGE_MODEL: str | None = os.getenv("JUDGE_MODEL")
PROMPT_VERSION: str = os.getenv("PROMPT_VERSION", "v1")
