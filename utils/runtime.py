"""Runtime paths and lightweight rotating logging for GazeLink."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys


APP_NAME = "GazeLink"


def _source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def get_resource_path(relative_path: str | os.PathLike[str]) -> Path:
    """Resolve a read-only bundled resource in source or PyInstaller mode."""
    base = Path(getattr(sys, "_MEIPASS", _source_root()))
    return (base / Path(relative_path)).resolve()


def get_data_root() -> Path:
    """Return the persistent writable root without using machine-specific paths."""
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / APP_NAME
    return _source_root()


def get_data_path(relative_path: str | os.PathLike[str]) -> Path:
    """Resolve persistent application data and create only its parent folder."""
    target = get_data_root() / Path(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def setup_logging(
    version: str, log_relative_path: str | os.PathLike[str]
) -> logging.Logger:
    """Configure one bounded UTF-8 application log and return its logger."""
    logger = logging.getLogger("gazelink")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        logger.propagate = False
        try:
            log_path = get_data_path(log_relative_path)
            handler: logging.Handler = RotatingFileHandler(
                log_path,
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
            )
        except OSError as exc:
            handler = logging.StreamHandler()
            handler.setLevel(logging.WARNING)
            print(f"GazeLink log file unavailable: {exc}")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
    logger.info("application started version=%s", version)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("gazelink")
