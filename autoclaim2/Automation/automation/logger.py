"""
logger.py — Structured logging system.

Provides:
  - Rotating text log  (human-readable, one line per event)
  - JSON Lines log     (machine-readable, for post-processing)
  - Thread-safe queue sink so the main loop is never blocked by I/O
  - Screenshot-on-error helper
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import numpy as np


# ---------------------------------------------------------------------------
# Public log-event keys (used throughout the codebase)
# ---------------------------------------------------------------------------
class Event:
    ROW_DETECTED       = "row_detected"
    CLAIM_FOUND        = "claim_found"
    CLAIM_CELL_CLICKED = "claim_cell_clicked"
    SELECT_CLICKED     = "select_clicked"
    CLAIM_VERIFIED     = "claim_verified"
    CLAIM_STOLEN       = "claim_stolen"
    CONNECT_CLICKED    = "connect_clicked"
    TIMEOUT            = "timeout"
    ERROR              = "error"
    STATE_CHANGE       = "state_change"
    LOOP_TICK          = "loop_tick"
    CALIBRATION        = "calibration"


# ---------------------------------------------------------------------------
# JSON Lines writer (non-blocking via background thread)
# ---------------------------------------------------------------------------
class _JsonlWriter(threading.Thread):
    def __init__(self, path: Path) -> None:
        super().__init__(daemon=True, name="JsonlWriter")
        self._path = path
        self._q: queue.Queue[dict | None] = queue.Queue()
        self.start()

    def put(self, record: dict) -> None:
        self._q.put(record)

    def run(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            while True:
                item = self._q.get()
                if item is None:  # sentinel → shutdown
                    break
                fh.write(json.dumps(item, default=str) + "\n")
                fh.flush()

    def stop(self) -> None:
        self._q.put(None)


# ---------------------------------------------------------------------------
# Main Logger class
# ---------------------------------------------------------------------------
class Logger:
    """
    Central logger for the automation tool.

    Usage::

        log = Logger(cfg.logging_cfg)
        log.info("Scanning started")
        log.event(Event.CLAIM_FOUND, row_id=2, text="CLAIM")
        log.screenshot_on_error(frame_array)
    """

    def __init__(self, logging_cfg: dict) -> None:
        self._cfg = logging_cfg
        self._log_dir = Path(logging_cfg.get("log_dir", "automation/logs"))
        self._ss_dir = Path(logging_cfg.get("screenshot_dir", "automation/screenshots"))
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._ss_dir.mkdir(parents=True, exist_ok=True)

        level_str = logging_cfg.get("level", "INFO").upper()
        level = getattr(logging, level_str, logging.INFO)

        # --- text logger ---
        today = datetime.now().strftime("%Y%m%d")
        log_file = self._log_dir / f"automation_{today}.log"

        self._logger = logging.getLogger("teleops")
        self._logger.setLevel(level)
        self._logger.handlers.clear()

        # File handler (rotating)
        max_bytes = int(logging_cfg.get("max_log_size_mb", 10)) * 1024 * 1024
        backup_count = int(logging_cfg.get("max_log_files", 7))
        fh = RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        self._logger.addHandler(fh)

        # Console handler
        ch = logging.StreamHandler()
        ch.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)-5s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        ch.setLevel(level)
        self._logger.addHandler(ch)

        # --- JSON lines writer ---
        jsonl_file = self._log_dir / f"events_{today}.jsonl"
        self._jsonl = _JsonlWriter(jsonl_file)

    # ------------------------------------------------------------------
    # Standard log levels
    # ------------------------------------------------------------------

    def debug(self, msg: str, **kw) -> None:
        self._logger.debug(msg, **({"extra": kw} if kw else {}))

    def info(self, msg: str, **kw) -> None:
        self._logger.info(msg)

    def warning(self, msg: str, **kw) -> None:
        self._logger.warning(msg)

    def error(self, msg: str, **kw) -> None:
        self._logger.error(msg)

    def critical(self, msg: str, **kw) -> None:
        self._logger.critical(msg)

    # ------------------------------------------------------------------
    # Structured event logging
    # ------------------------------------------------------------------

    def event(self, event_type: str, **data: Any) -> None:
        """Emit a structured event to both text and JSON Lines logs."""
        record = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "event": event_type,
            **data,
        }
        self._jsonl.put(record)
        msg = f"[{event_type}] " + " | ".join(f"{k}={v}" for k, v in data.items())
        self._logger.info(msg)

    # ------------------------------------------------------------------
    # Screenshot helpers
    # ------------------------------------------------------------------

    def screenshot_on_error(self, frame: np.ndarray | None, tag: str = "error") -> None:
        """Save *frame* to the screenshots directory if enabled in config."""
        if frame is None:
            return
        try:
            import cv2  # lazy import — keep logger startup fast
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            out = self._ss_dir / f"{tag}_{ts}.png"
            cv2.imwrite(str(out), frame)
            self._logger.info(f"Screenshot saved → {out}")
        except Exception as exc:
            self._logger.warning(f"Failed to save screenshot: {exc}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Flush and close background writer."""
        self._jsonl.stop()
        logging.shutdown()
