"""Configuration loader — the single source of truth for every threshold,
timing, address and port in the system.

Per CLAUDE.md principle 5 ("config, not code"), nothing else in this codebase
hard-codes a magic number or an address. Every module is constructed from a slice
of the dict this module returns.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# Repo root is one level up from src/. Exported so other modules resolve
# relative paths (logs/, config.yaml) against a stable anchor regardless of cwd.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config.yaml"
EXAMPLE_CONFIG = REPO_ROOT / "config.example.yaml"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load and return the configuration dict.

    Defaults to ``config.yaml`` at the repo root. Fails with an actionable
    message if it is missing, rather than silently falling back to defaults —
    an unexpected default in an exhibition is worse than a clear stop.
    """
    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"{cfg_path} not found. Copy the template and edit it:\n"
            f"    cp {EXAMPLE_CONFIG.name} {DEFAULT_CONFIG.name}"
        )
    with cfg_path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not isinstance(cfg, dict):
        raise ValueError(f"{cfg_path} did not parse to a mapping")
    return cfg
