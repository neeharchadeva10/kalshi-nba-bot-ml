"""Settings loader. Secrets come only from environment / .env and are never logged."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
# Environment variables that hold secrets (never printed, logged or stored). KALSHI_ENV only names the environment
# (demo | prod) and is not a secret. The execution package's env module reads exactly the names listed below.
SECRET_KEYS = {
    "KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PATH",                                # legacy single pair (read for prod)
    "KALSHI_PROD_KEY_ID", "KALSHI_PROD_KEY_PATH", "KALSHI_PROD_KEY_PASSWORD",
    "KALSHI_DEMO_KEY_ID", "KALSHI_DEMO_KEY_PATH", "KALSHI_DEMO_KEY_PASSWORD",
    "ANTHROPIC_API_KEY",
}


@lru_cache(maxsize=1)
def settings(path: str | None = None) -> dict[str, Any]:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    cfg_path = Path(path) if path else PROJECT_ROOT / "config" / "settings.yaml"
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    for key, rel in cfg["paths"].items():
        p = Path(rel)
        cfg["paths"][key] = str(p if p.is_absolute() else PROJECT_ROOT / p)
    return cfg


def path(key: str) -> Path:
    p = Path(settings()["paths"][key])
    if key.endswith("_dir"):
        p.mkdir(parents=True, exist_ok=True)
    return p


def secret(name: str) -> str | None:
    """Read a secret from env/.env. Callers must never print or log the value."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    return os.environ.get(name) or None
