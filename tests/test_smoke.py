# Phase 1 – Step 1.1: smoke tests proving the toolchain, imports and config wiring work

from src import config


# Phase 1 – Step 1.1: config paths are anchored at the repo root
def test_paths_resolve_from_project_root():
    assert config.DATA_DIR.is_absolute()
    assert config.LOG_DIR.is_absolute()
    assert (config.PROJECT_ROOT / "pyproject.toml").exists()


# Phase 1 – Step 1.1: the provided CSVs are where config says they are
def test_data_files_present():
    for name in ("customers.csv", "accounts.csv", "transactions.csv"):
        assert (config.DATA_DIR / name).exists(), f"missing {name} in {config.DATA_DIR}"


# Phase 1 – Step 1.1: PROMPT_VERSION always has a value (it is stamped on every audit record)
def test_prompt_version_has_default():
    assert config.PROMPT_VERSION
