"""
clicker.py — MouseController
Safe, debounced mouse and keyboard control using pyautogui.

Safety features:
  - Minimum debounce interval between any two clicks
  - Click history with TTL prevents double-clicking the same target
  - pyautogui FAILSAFE (move mouse to top-left corner to abort)
  - Human-like random offset on click position
  - Smooth movement (no teleporting)
"""

from __future__ import annotations

import random
import time
import os
import msvcrt
from collections import deque
from typing import Optional

import pyautogui

# Disable pyautogui's own pause (we manage timing ourselves)
pyautogui.PAUSE = 0.0


class MouseController:
    """
    Wraps pyautogui with safety and debounce logic.

    Usage::

        mc = MouseController(mouse_cfg, timing_cfg)
        mc.click(x=500, y=300, label="select_btn_row_2")
        mc.click(x=600, y=200, label="connect_btn")
    """

    def __init__(self, mouse_cfg: dict, timing_cfg: dict) -> None:
        self._move_duration: float = float(mouse_cfg.get("move_duration_s", 0.15))
        self._random_offset: int = int(mouse_cfg.get("randomize_offset_px", 3))
        self._failsafe: bool = bool(mouse_cfg.get("failsafe", True))

        self._debounce_ms: int = int(timing_cfg.get("debounce_ms", 500))
        self._post_click_wait_ms: int = int(timing_cfg.get("post_click_wait_ms", 500))

        pyautogui.FAILSAFE = self._failsafe

        # Click history: deque of (label, timestamp)
        # Prevents re-clicking the same logical target within a TTL window
        self._click_history: deque[tuple[str, float]] = deque(maxlen=50)
        self._last_click_time: float = 0.0

    # ------------------------------------------------------------------
    # Public click API
    # ------------------------------------------------------------------

    def click(
        self,
        x: int,
        y: int,
        label: str = "",
        ttl_ms: int = 2000,
        button: str = "left",
    ) -> bool:
        """
        Move to (x, y) and left-click.

        Parameters
        ----------
        x, y:
            Absolute screen coordinates.
        label:
            Logical label for this click target (used for dedup).
            If a click with the same label occurred within *ttl_ms*,
            this call is a no-op and returns False.
        ttl_ms:
            Time-to-live for the dedup window (milliseconds).
        button:
            Mouse button — 'left', 'right', or 'middle'.

        Returns
        -------
        bool
            True if the click was executed, False if suppressed.
        """
        now = time.perf_counter()

        # Debounce: enforce minimum interval between any clicks
        elapsed_since_last = (now - self._last_click_time) * 1000
        if elapsed_since_last < self._debounce_ms:
            wait_s = (self._debounce_ms - elapsed_since_last) / 1000
            time.sleep(wait_s)
            now = time.perf_counter()

        # TTL dedup: skip if same label was clicked recently
        if label and self._was_recently_clicked(label, ttl_ms):
            return False

        # Add human-like random jitter
        jitter_x = random.randint(-self._random_offset, self._random_offset)
        jitter_y = random.randint(-self._random_offset, self._random_offset)
        target_x = x + jitter_x
        target_y = y + jitter_y

        lock_file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'mouse.lock')
        with open(lock_file_path, 'w') as lf:
            while True:
                try:
                    msvcrt.locking(lf.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except IOError:
                    time.sleep(0.01)
                    
            try:
                try:
                    pyautogui.moveTo(target_x, target_y, duration=self._move_duration)
                    pyautogui.click(button=button)
                except pyautogui.FailSafeException:
                    raise  # re-raise so the caller can handle graceful shutdown
                except Exception as exc:
                    raise RuntimeError(f"Click failed at ({target_x}, {target_y}): {exc}") from exc
            finally:
                msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)

        # Record click
        self._last_click_time = time.perf_counter()
        if label:
            self._click_history.append((label, self._last_click_time))

        # Post-click wait
        if self._post_click_wait_ms > 0:
            time.sleep(self._post_click_wait_ms / 1000)

        return True

    def move_to(self, x: int, y: int) -> None:
        """Smoothly move cursor to (x, y) without clicking."""
        pyautogui.moveTo(x, y, duration=self._move_duration)

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def was_clicked(self, label: str, within_ms: int = 2000) -> bool:
        """Return True if *label* was clicked within the last *within_ms* ms."""
        return self._was_recently_clicked(label, within_ms)

    def clear_history(self) -> None:
        """Clear click history (e.g. after a successful CONNECTED state)."""
        self._click_history.clear()

    def clear_label(self, label: str) -> None:
        """Remove all history entries matching *label*."""
        keep = [(lbl, ts) for (lbl, ts) in self._click_history if lbl != label]
        self._click_history.clear()
        self._click_history.extend(keep)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _was_recently_clicked(self, label: str, ttl_ms: int) -> bool:
        now = time.perf_counter()
        ttl_s = ttl_ms / 1000
        for lbl, ts in self._click_history:
            if lbl == label and (now - ts) < ttl_s:
                return True
        return False
