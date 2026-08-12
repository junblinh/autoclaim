"""
capture.py — ScreenCapture
High-performance partial screen capture using MSS.

Performance target: < 10 ms per frame.
"""

from __future__ import annotations

import time
from typing import Optional

import mss
import mss.tools
import numpy as np


class ScreenCapture:
    """
    Captures a configurable screen region at high speed using MSS.

    The MSS instance is kept open between calls to avoid per-call startup
    overhead.  Thread-safe: each thread should own its own ScreenCapture
    instance (MSS is not thread-safe).

    Usage::

        sc = ScreenCapture(table_region={"left": 100, "top": 200, "width": 800, "height": 400})
        frame = sc.capture()          # returns BGR numpy array
        frame = sc.capture_region({"left": 110, "top": 210, "width": 200, "height": 30})
    """

    def __init__(
        self,
        table_region: dict,
        monitor_index: int = 1,
    ) -> None:
        """
        Parameters
        ----------
        table_region:
            Dict with keys left, top, width, height (absolute screen coordinates).
        monitor_index:
            MSS monitor index.  1 = primary monitor.
        """
        self._region = dict(table_region)  # defensive copy
        self._monitor_index = monitor_index
        self._sct = mss.mss()

        # FPS tracking
        self._frame_times: list[float] = []
        self._fps: float = 0.0
        self._fps_window: int = 30  # rolling average over N frames

        # Frame hash for change detection (used by OCR cache)
        self._last_hash: int = 0

    # ------------------------------------------------------------------
    # Region management
    # ------------------------------------------------------------------

    def set_region(self, region: dict) -> None:
        """Update capture region at runtime (e.g. after calibration)."""
        self._region = dict(region)

    @property
    def region(self) -> dict:
        return dict(self._region)

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(self) -> np.ndarray:
        """
        Capture the configured table region.

        Returns
        -------
        np.ndarray
            BGR image (height × width × 3).
        """
        return self._grab(self._region)

    def capture_region(self, region: dict) -> np.ndarray:
        """
        Capture an arbitrary sub-region (absolute screen coordinates).

        Useful for isolating the CLAIM column or a single row cell.
        """
        return self._grab(region)

    def capture_claim_column(self, claim_column: dict) -> np.ndarray:
        """Convenience: capture only the CLAIM column region."""
        return self._grab(claim_column)

    def is_changed(self, frame: np.ndarray, tolerance: int = 5) -> bool:
        """
        Return True if *frame* differs from the previous capture.

        Uses a fast hash of a downsampled version of the frame so identical
        frames (no GUI change) skip the expensive OCR step.
        """
        small = frame[::4, ::4]  # 1/16 pixels
        new_hash = hash(small.tobytes())
        changed = abs(new_hash - self._last_hash) != 0
        self._last_hash = new_hash
        return changed

    # ------------------------------------------------------------------
    # FPS
    # ------------------------------------------------------------------

    def fps(self) -> float:
        """Return rolling-average FPS of capture calls."""
        return self._fps

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _grab(self, region: dict) -> np.ndarray:
        """Grab region from screen and return BGR numpy array."""
        t0 = time.perf_counter()

        # MSS expects: {"left", "top", "width", "height"}
        monitor = {
            "left": int(region["left"]),
            "top": int(region["top"]),
            "width": int(region["width"]),
            "height": int(region["height"]),
        }
        sct_img = self._sct.grab(monitor)

        # Convert BGRA → BGR
        frame = np.array(sct_img)[:, :, :3]  # drop alpha channel

        # Update FPS
        elapsed = time.perf_counter() - t0
        self._frame_times.append(elapsed)
        if len(self._frame_times) > self._fps_window:
            self._frame_times.pop(0)
        avg = sum(self._frame_times) / len(self._frame_times)
        self._fps = 1.0 / avg if avg > 0 else 0.0

        return frame

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release the MSS context."""
        self._sct.close()

    def __enter__(self) -> "ScreenCapture":
        return self

    def __exit__(self, *_) -> None:
        self.close()
