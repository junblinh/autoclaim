"""
workflow.py — WorkflowEngine  (v3)
Professional desktop automation FSM.

Design goals
------------
* Claim EXACTLY ONE bot per session, then enter STOPPED state.
* Never touch the mouse unless a CLAIM row is confirmed.
* Ignore CLAIM text outside the calibrated table region (headers, labels).
* Deduplicate attempted rows by absolute Y coordinate (survives frame-to-frame
  row renumbering).
* Hotkey-driven enable / disable / pause via thread-safe boolean flags.
* StatusWindow receives live updates after every state change.

FSM States
----------
IDLE          Initial state (before first start)
SCANNING      Polling the CLAIM column for available bots
CLAIM_FOUND   A valid CLAIM cell was located
CLICK_CLAIM   Clicking the CLAIM cell
WAIT_USERNAME Polling OCR on just that cell, waiting for our username
CLAIM_SUCCESS Our username confirmed — proceed with Select + Connect
CLICK_SELECT  Clicked the Select button
CLICK_CONNECT Clicking the fixed Connect button
CONNECTED     Connection established — enter STOPPED
STOPPED       Terminal state — no scanning until manually re-enabled (F8)
ERROR         Unhandled exception — auto-recover to SCANNING

Transition matrix
-----------------
IDLE          → SCANNING
SCANNING      → CLAIM_FOUND | SCANNING | STOPPED | ERROR
CLAIM_FOUND   → CLICK_CLAIM | SCANNING | ERROR
CLICK_CLAIM   → WAIT_USERNAME | ERROR
WAIT_USERNAME → CLAIM_SUCCESS | SCANNING | ERROR
CLAIM_SUCCESS → CLICK_SELECT | ERROR
CLICK_SELECT  → CLICK_CONNECT | ERROR
CLICK_CONNECT → CONNECTED | ERROR
CONNECTED     → STOPPED
STOPPED       → SCANNING   (only via enable())
ERROR         → SCANNING | STOPPED
"""

from __future__ import annotations

import threading
import time
import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Optional

import numpy as np
from rapidfuzz import fuzz

from .capture import ScreenCapture
from .clicker import MouseController
from .config import ConfigManager
from .logger import Event, Logger
from .ocr import OCRReader, OCRResult
from .vision import ClaimDetector, RowInfo


# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------

class State(Enum):
    IDLE          = auto()
    SCANNING      = auto()
    CLAIM_FOUND   = auto()
    CLICK_CLAIM   = auto()
    WAIT_USERNAME = auto()
    CLAIM_SUCCESS = auto()
    CLICK_SELECT  = auto()
    CLICK_CONNECT = auto()
    CONNECTED     = auto()
    STOPPED       = auto()
    ERROR         = auto()


_VALID_TRANSITIONS: dict[State, set[State]] = {
    State.IDLE:          {State.SCANNING},
    State.SCANNING:      {State.CLAIM_FOUND, State.SCANNING, State.STOPPED, State.ERROR},
    State.CLAIM_FOUND:   {State.CLICK_CLAIM, State.SCANNING, State.ERROR},
    State.CLICK_CLAIM:   {State.WAIT_USERNAME, State.SCANNING, State.ERROR},
    State.WAIT_USERNAME: {State.CLAIM_SUCCESS, State.SCANNING, State.ERROR},
    State.CLAIM_SUCCESS: {State.CLICK_SELECT, State.ERROR},
    State.CLICK_SELECT:  {State.CLICK_CONNECT, State.ERROR},
    State.CLICK_CONNECT: {State.CONNECTED, State.ERROR},
    State.CONNECTED:     {State.STOPPED},
    State.STOPPED:       {State.SCANNING},
    State.ERROR:         {State.SCANNING, State.STOPPED},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _Target:
    """Encapsulates one attempt at a CLAIM row."""
    row: RowInfo
    ocr: OCRResult
    claim_abs: tuple[int, int]   # absolute screen position of CLAIM cell centre
    y_key: int                   # rounded Y for dedup


class WorkflowEngine:
    """
    Main automation controller.

    Thread model
    ------------
    * start() blocks the calling thread (the main thread).
    * StatusWindow runs in its own daemon thread.
    * HotkeyManager runs in pynput's daemon thread.
    * enable/disable/pause_resume are called from the hotkey thread and
      write simple boolean flags — safe under Python's GIL.

    Usage::

        engine = WorkflowEngine(cfg, logger, on_update=status_window.update)
        engine.start()   # blocks
    """

    # Y-coordinate snap grid for dedup (pixels)
    _Y_SNAP = 8

    def __init__(
        self,
        cfg: ConfigManager,
        logger: Logger,
        on_update: Optional[Callable[..., None]] = None,
    ) -> None:
        self._cfg = cfg
        self._log = logger
        self._on_update = on_update   # called after every state change

        # ── Control flags (written by hotkey thread, read by main loop) ──
        self._enabled: bool = True    # F8 — overall on/off
        self._paused: bool = False    # F10 — pause without stopping
        self._stop_event = threading.Event()

        # ── FSM ──
        self._state: State = State.IDLE
        self._state_lock = threading.Lock()
        self._state_entered_at: float = 0.0

        # ── Current claim target ──
        self._target: Optional[_Target] = None

        # ── Per-session dedup: absolute Y positions already attempted ──
        # Keyed on round(abs_y / _Y_SNAP) * _Y_SNAP
        self._attempted_y: set[int] = set()

        # ── Sub-systems ──
        self._capture: Optional[ScreenCapture] = None
        self._ocr: Optional[OCRReader] = None
        self._detector: Optional[ClaimDetector] = None
        self._clicker: Optional[MouseController] = None

        # ── Perf tracking ──
        self._loop_times: list[float] = []
        self._fps: float = 0.0

    # ------------------------------------------------------------------
    # Hotkey-safe control API  (called from pynput thread)
    # ------------------------------------------------------------------

    def enable(self) -> None:
        """F8 ON — resume scanning from STOPPED or after a pause."""
        self._paused = False
        self._enabled = True
        self._attempted_y.clear()   # fresh session
        self._log.info("Automation ENABLED (F8)")
        # If currently STOPPED, immediately begin scanning
        if self.state == State.STOPPED:
            self._transition(State.SCANNING)
        self._notify()

    def disable(self) -> None:
        """F8 OFF — stop scanning, enter STOPPED state."""
        self._enabled = False
        self._paused = False
        self._log.info("Automation DISABLED (F8)")
        # Do not call _transition() directly from the hotkey thread, 
        # let the _tick loop handle it safely to prevent race conditions.
        self._notify()

    def toggle(self) -> None:
        """F8 — toggle between enabled and stopped."""
        if self._enabled:
            self.disable()
        else:
            self.enable()

    def pause_resume(self) -> None:
        """F10 — pause or resume scanning without leaving SCANNING state."""
        self._paused = not self._paused
        action = "PAUSED" if self._paused else "RESUMED"
        self._log.info(f"Automation {action} (F10)")
        self._notify()

    def emergency_stop(self) -> None:
        """F9 — immediately stop the engine and exit the process."""
        self._log.info("Emergency stop (F9) — exiting.")
        self._stop_event.set()

    def safe_exit(self) -> None:
        """ESC — clean shutdown."""
        self._log.info("Clean exit requested (ESC).")
        self._stop_event.set()

    @property
    def state(self) -> State:
        with self._state_lock:
            return self._state

    @property
    def fps(self) -> float:
        return self._fps

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._init_subsystems()
        self._transition(State.SCANNING)
        self._log.info(
            f"WorkflowEngine started — user={self._cfg.username!r}  "
            f"table={self._cfg.table_region}"
        )
        try:
            self._loop()
        except KeyboardInterrupt:
            self._log.info("Interrupted by user (Ctrl-C).")
        finally:
            self._shutdown()

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        interval_s = self._cfg.timing.get("loop_interval_ms", 100) / 1000

        while not self._stop_event.is_set():
            t0 = time.perf_counter()

            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001
                self._log.error(f"Unhandled loop exception: {exc}")
                self._log.event(Event.ERROR, error=str(exc))
                frame = self._safe_capture()
                if frame is not None and self._cfg.debug_cfg.get("save_screenshots_on_error"):
                    self._log.screenshot_on_error(frame, "loop_error")
                self._transition(State.ERROR)
                self._handle_error()

            elapsed = time.perf_counter() - t0
            self._update_fps(elapsed)
            sleep_s = max(0.0, interval_s - elapsed)
            if sleep_s > 0:
                time.sleep(sleep_s)

    def _tick(self) -> None:
        s = self.state

        # ── STOPPED: sleep cheaply, no work ──
        if s == State.STOPPED:
            time.sleep(0.2)
            return

        # ── PAUSED: keep scanning but don't act ──
        if self._paused and s == State.SCANNING:
            time.sleep(0.1)
            return

        # ── Disabled but not yet STOPPED: transition ──
        if not self._enabled and s != State.STOPPED:
            self._transition_safe_to_stopped()
            return

        # ── Global state timeout (except long-running states) ──
        exempt = {State.IDLE, State.SCANNING, State.WAIT_USERNAME,
                  State.CONNECTED, State.STOPPED, State.ERROR}
        global_timeout = self._cfg.timing.get("state_timeout_s", 10)
        if s not in exempt:
            if time.perf_counter() - self._state_entered_at > global_timeout:
                self._log.warning(f"Global timeout in {s.name}")
                self._log.event(Event.TIMEOUT, state=s.name)
                self._target = None
                self._transition(State.ERROR)
                return

        dispatch = {
            State.SCANNING:      self._do_scanning,
            State.CLAIM_FOUND:   self._do_claim_found,
            State.CLICK_CLAIM:   self._do_click_claim,
            State.WAIT_USERNAME: self._do_wait_username,
            State.CLAIM_SUCCESS: self._do_claim_success,
            State.CLICK_SELECT:  self._do_click_select,
            State.CLICK_CONNECT: self._do_click_connect,
            State.CONNECTED:     self._do_connected,
            State.ERROR:         self._handle_error,
        }
        handler = dispatch.get(s)
        if handler:
            handler()

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _do_scanning(self) -> None:
        """
        Capture the table region and OCR the CLAIM column.
        Only acts on rows whose CLAIM cell falls within the calibrated
        claim_column bounds (excludes headers above claim_column.top).
        """
        if not self._cfg.is_calibrated():
            self._log.error("Calibration missing or invalid. Please run 'Calibrate Screen'.")
            self._enabled = False
            self._transition(State.STOPPED)
            return

        frame = self._capture.capture()
        if not self._capture.is_changed(frame):
            return   # identical frame — skip expensive OCR

        rows = self._detector.detect_rows(frame)
        if not rows:
            return

        claim_col_frame, _ = self._crop_claim_column(frame)
        row_rects = [{"y": r.rect[1], "height": r.rect[3]} for r in rows]

        # Run OCR on CLAIM column only
        claim_results = self._ocr.find_claim_rows(claim_col_frame, row_rects)
        if not claim_results:
            self._log.event(Event.LOOP_TICK, state="SCANNING",
                            rows=len(rows), claims=0)
            return

        self._log.event(Event.ROW_DETECTED,
                        total_rows=len(rows),
                        claim_rows=[r.row_id for r in claim_results])

        # ── Pick first valid, unambiguous CLAIM row ──
        for ocr in claim_results:
            if ocr.row_id < 0:
                continue

            # Calculate absolute screen position of this CLAIM cell
            abs_x, abs_y = self._claim_cell_abs(ocr)

            # ── Header exclusion ──
            # Reject any CLAIM cell above (or within margin of) the
            # calibrated claim_column top.  This filters out GUI headers,
            # grey labels, and title rows that are outside the actual data.
            claim_col_top = int(self._cfg.claim_column.get("top", 0))
            header_margin = int(self._cfg.get("header_exclusion_px", 5))
            if abs_y < claim_col_top + header_margin:
                self._log.debug(
                    f"Skipping header-zone CLAIM at abs_y={abs_y} "
                    f"(claim_col_top={claim_col_top})"
                )
                continue

            # ── Y-based dedup ──
            y_key = round(abs_y / self._Y_SNAP) * self._Y_SNAP
            if y_key in self._attempted_y:
                continue   # already tried this row in this session

            # ── Find matching RowInfo ──
            matching = [r for r in rows if r.index == ocr.row_id]
            if not matching:
                if ocr.row_id < len(rows):
                    matching = [rows[ocr.row_id]]
            if not matching:
                continue

            row = matching[0]

            # ── SITE BLOCKLIST CHECK ──
            blocklist = self._cfg.get("site_blocklist", [])
            if blocklist and self._cfg.get("site_column"):
                site_text = self._ocr_site_cell(frame, row)
                if site_text and any(b.upper() in site_text.upper() for b in blocklist if b):
                    self._log.debug(
                        f"Skipping row_id={row.index} because SITE '{site_text}' is blocklisted."
                    )
                    continue

            # ── TELEOP TYPE CHECK ──
            if self._cfg.get("type_column"):
                type_text = self._ocr_type_cell(frame, row)
                allowed_types = [t.upper() for t in self._cfg.allowed_teleop_types if t]
                if not any(t in type_text.upper() for t in allowed_types):
                    self._log.debug(
                        f"Skipping row_id={row.index} because TYPE is '{type_text}' (not in {allowed_types})."
                    )
                    continue

            # ── ALARM TIME CHECK ──
            if self._cfg.get("alarm_column"):
                alarm_text = self._ocr_alarm_cell(frame, row)
                if alarm_text.strip():
                    alarm_seconds = self._parse_time_str(alarm_text)
                    max_seconds = self._parse_time_str(self._cfg.max_alarm_time)
                    if alarm_seconds is not None and max_seconds is not None:
                        if alarm_seconds > max_seconds:
                            self._log.info(
                                f"Skipping row_id={row.index} because ALARM TIME '{alarm_text}' ({alarm_seconds}s) is above limit '{self._cfg.max_alarm_time}' ({max_seconds}s)."
                            )
                            continue

            select_pos = self._detector.find_select_button(row, frame)
            row.select_btn_pos = select_pos
            row.ocr_text = ocr.text
            row.ocr_confidence = ocr.confidence

            self._target = _Target(
                row=row,
                ocr=ocr,
                claim_abs=(abs_x, abs_y),
                y_key=y_key,
            )

            self._log.event(Event.CLAIM_FOUND,
                            row_id=ocr.row_id,
                            text=ocr.text,
                            confidence=round(ocr.confidence, 3),
                            claim_cell=(abs_x, abs_y))
            self._notify(ocr_text=ocr.text)
            self._transition(State.CLAIM_FOUND)
            return

    def _do_claim_found(self) -> None:
        if self._target is None:
            self._log.warning("CLAIM_FOUND with no target — back to SCANNING")
            self._transition(State.SCANNING)
            return
        self._transition(State.CLICK_CLAIM)

    def _do_click_claim(self) -> None:
        x, y = self._target.claim_abs
        label = f"claim_cell_y{self._target.y_key}"

        clicked = self._clicker.click(x, y, label=label, ttl_ms=3000)
        if not clicked:
            self._log.warning("CLAIM cell click suppressed (debounce)")
            self._clear_target()
            self._transition(State.SCANNING)
            return

        self._log.event(Event.CLAIM_CELL_CLICKED,
                        row_id=self._target.row.index, x=x, y=y)
        self._transition(State.WAIT_USERNAME)

    def _do_wait_username(self) -> None:
        """
        Poll OCR on the exact CLAIM cell.
        Accept: our username        → CLAIM_SUCCESS
        Keep polling: 'claiming' or 'CLAIM'
        Reject: another user's name → SCANNING (mark as attempted)
        Timeout                     → STOPPED (prevent double claims)
        """
        timeout_s = self._cfg.timing.get("claim_click_timeout_s", 2.0)
        elapsed = time.perf_counter() - self._state_entered_at

        if elapsed > timeout_s:
            self._log.warning(
                f"WAIT_USERNAME timed out ({timeout_s}s) "
                f"— row y={self._target.y_key}. Stopping to prevent double claims."
            )
            self._log.event(Event.TIMEOUT, state="WAIT_USERNAME",
                            y_key=self._target.y_key)
            self._attempted_y.add(self._target.y_key)
            self._clear_target()
            self._transition_safe_to_stopped()
            return

        cell_frame = self._capture_claim_cell()
        if cell_frame is None:
            return

        text = self._ocr_single_cell(cell_frame)
        self._notify(ocr_text=text if text else "…")

        username = self._cfg.username.upper()
        text_clean = text.strip().upper()

        # Handle common OCR misreads (L vs I vs 1, O vs 0)
        def _norm(s: str) -> str:
            return s.replace('I', 'L').replace('1', 'L').replace('0', 'O')

        if username and _norm(text_clean) == _norm(username):
            # ✓ Claimed by us
            self._log.event(Event.CLAIM_VERIFIED,
                            y_key=self._target.y_key,
                            cell_text=text,
                            elapsed_s=round(elapsed, 3))
            self._transition(State.CLAIM_SUCCESS)
            return

        # Check if it is a pending / claiming state
        if self._is_pending_state(text):
            # Still pending, stay in WAIT_USERNAME and keep polling
            return

        # ✗ Cell changed to someone else's name (not pending and not our username)
        self._log.warning(
            f"Cell shows '{text}' (not pending and not '{username}') — claimed by someone else."
        )
        self._log.event(Event.CLAIM_STOLEN,
                        y_key=self._target.y_key, cell_text=text)
        self._attempted_y.add(self._target.y_key)
        self._clear_target()
        self._transition(State.SCANNING)

        # else: still showing "CLAIM" or blank — keep polling

    def _do_claim_success(self) -> None:
        """Click the Select button on the claimed row."""
        row = self._target.row
        if row.select_btn_pos is None:
            self._log.error("CLAIM_SUCCESS: Select button position unknown")
            self._transition(State.ERROR)
            return

        x, y = row.select_btn_pos
        clicked = self._clicker.click(
            x, y, label=f"select_y{self._target.y_key}", ttl_ms=6000
        )
        if not clicked:
            self._log.warning("Select click suppressed")
            self._transition(State.ERROR)
            return

        self._log.event(Event.SELECT_CLICKED,
                        y_key=self._target.y_key, x=x, y=y)

        # Brief pause between Select and Connect
        post_select_ms = self._cfg.timing.get("post_select_wait_ms", 200)
        time.sleep(post_select_ms / 1000)

        self._transition(State.CLICK_SELECT)

    def _do_click_select(self) -> None:
        """Select already clicked in _do_claim_success — proceed to Connect."""
        self._transition(State.CLICK_CONNECT)

    def _do_click_connect(self) -> None:
        """Click the fixed Connect button configured in config.json."""
        cb = self._cfg.connect_button
        x  = int(cb.get("x", 0))
        y  = int(cb.get("y", 0))

        clicked = self._clicker.click(
            x, y, label=f"connect_y{self._target.y_key}", ttl_ms=6000
        )
        if not clicked:
            self._log.warning("Connect click suppressed")
            self._transition(State.ERROR)
            return

        self._log.event(Event.CONNECT_CLICKED,
                        y_key=self._target.y_key, x=x, y=y)
        self._transition(State.CONNECTED)

    def _do_connected(self) -> None:
        """
        Successfully connected — stop the automation.
        One bot claimed per session. F8 required to re-enable.
        """
        post_ms = self._cfg.timing.get("post_connect_wait_ms", 1000)
        self._log.info(
            f"✓ Connected! Waiting {post_ms}ms then entering STOPPED state."
        )
        time.sleep(post_ms / 1000)

        self._enabled = False          # require manual F8 to re-enable
        self._clicker.clear_history()
        self._clear_target()
        self._attempted_y.clear()
        self._transition(State.STOPPED)
        self._notify()

    # ------------------------------------------------------------------
    # Error recovery
    # ------------------------------------------------------------------

    def _handle_error(self) -> None:
        self._log.warning("ERROR — stopping automation for safety.")
        time.sleep(1.0)
        self._clear_target()
        self._attempted_y.clear()
        self._clicker.clear_history()
        self._transition_safe_to_stopped()

    # ------------------------------------------------------------------
    # FSM transition
    # ------------------------------------------------------------------

    def _transition(self, new_state: State) -> None:
        with self._state_lock:
            old = self._state
            allowed = _VALID_TRANSITIONS.get(old, set())
            if new_state not in allowed:
                self._log.warning(
                    f"Illegal FSM transition {old.name} → {new_state.name} — ignored"
                )
                return
            self._state = new_state
            self._state_entered_at = time.perf_counter()

        self._log.event(Event.STATE_CHANGE,
                        from_state=old.name, to_state=new_state.name)
        self._notify()

    def _transition_safe_to_stopped(self) -> None:
        """Force transition to STOPPED regardless of current state."""
        with self._state_lock:
            old = self._state
            self._state = State.STOPPED
            self._state_entered_at = time.perf_counter()
        self._log.event(Event.STATE_CHANGE,
                        from_state=old.name, to_state=State.STOPPED.name)
        self._clear_target()
        self._notify()

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _claim_cell_abs(self, ocr: OCRResult) -> tuple[int, int]:
        """
        Convert an OCR bbox (relative to the cropped claim-col frame)
        to absolute screen coordinates.

        claim_col_frame is a vertical strip of the table frame starting at
        x = claim_column.left (absolute).  The table frame starts at
        y = table_region.top.

        abs_x = claim_column.left + bbox.x + bbox.w/2
        abs_y = table_region.top  + bbox.y + bbox.h/2
        """
        bx, by, bw, bh = ocr.bbox
        return (
            int(self._cfg.claim_column["left"]) + bx + bw // 2,
            int(self._cfg.table_region["top"])  + by + bh // 2,
        )

    def _crop_claim_column(self, frame: np.ndarray) -> tuple[np.ndarray, int]:
        """Slice the claim-column strip from the full table frame."""
        col = self._cfg.claim_column
        tbl = self._cfg.table_region
        x_local = max(0, int(col["left"]) - int(tbl["left"]))
        w       = int(col["width"])
        return frame[:, x_local: x_local + w], x_local

    def _capture_claim_cell(self) -> Optional[np.ndarray]:
        """Capture just the CLAIM cell area for fast per-cell OCR polling."""
        if self._target is None:
            return None
        try:
            bx, by, bw, bh = self._target.ocr.bbox
            col = self._cfg.claim_column
            tbl = self._cfg.table_region
            pad = 5
            region = {
                "left":   int(col["left"]) + max(0, bx - pad),
                "top":    int(tbl["top"])  + max(0, by - pad),
                "width":  bw + pad * 2,
                "height": bh + pad * 2,
            }
            return self._capture.capture_region(region)
        except Exception as exc:
            self._log.warning(f"capture_claim_cell failed: {exc}")
            return None

    def _ocr_site_cell(self, frame: np.ndarray, row: RowInfo) -> str:
        """Extract and OCR the SITE column for a specific row."""
        site_col = self._cfg.get("site_column")
        if not site_col:
            return ""
        
        tbl = self._cfg.table_region
        x_local = max(0, int(site_col["left"]) - int(tbl["left"]))
        w = int(site_col["width"])
        
        _, ry, _, rh = row.rect
        site_cell_frame = frame[ry : ry + rh, x_local : x_local + w]
        
        if site_cell_frame.size == 0:
            return ""
            
        return self._ocr_single_cell(site_cell_frame)

    def _ocr_type_cell(self, frame: np.ndarray, row: RowInfo) -> str:
        """Extract and OCR the TELEOP TYPE column for a specific row."""
        type_col = self._cfg.get("type_column")
        if not type_col:
            return ""
        
        tbl = self._cfg.table_region
        x_local = max(0, int(type_col["left"]) - int(tbl["left"]))
        w = int(type_col["width"])
        
        _, ry, _, rh = row.rect
        type_cell_frame = frame[ry : ry + rh, x_local : x_local + w]
        
        if type_cell_frame.size == 0:
            return ""
            
        return self._ocr_single_cell(type_cell_frame)

    def _ocr_single_cell(self, cell_frame: np.ndarray) -> str:
        """OCR a tiny single-cell crop; return best-confidence text."""
        try:
            results = self._ocr.read_claim_column(cell_frame, row_rects=None)
            if not results:
                return ""
            return max(results, key=lambda r: r.confidence).text.strip()
        except Exception as exc:
            self._log.warning(f"single-cell OCR failed: {exc}")
            return ""

    def _is_pending_state(self, text: str) -> bool:
        """Return True if text indicates the server is still processing the claim."""
        text_clean = text.strip().upper()
        if not text_clean:
            return True  # Blank is treated as pending/loading
            
        keyword = self._cfg.ocr_cfg.get("claim_keyword", "CLAIM").upper()
        if keyword in text_clean:
            return True
            
        pending_words = self._cfg.pending_keywords
        threshold = self._cfg.pending_match_threshold
        
        for pw in pending_words:
            pw_upper = pw.upper()
            if pw_upper in text_clean:
                return True
                
            # Fuzzy match ratio (returns 0-100)
            score = fuzz.ratio(text_clean, pw_upper)
            if score >= threshold:
                return True
                
        return False

    def _ocr_alarm_cell(self, frame: np.ndarray, row: RowInfo) -> str:
        """Extract and OCR the ALARMED FOR column for a specific row."""
        alarm_col = self._cfg.get("alarm_column")
        if not alarm_col:
            return ""
        
        tbl = self._cfg.table_region
        x_local = max(0, int(alarm_col["left"]) - int(tbl["left"]))
        w = int(alarm_col["width"])
        
        _, ry, _, rh = row.rect
        alarm_cell_frame = frame[ry : ry + rh, x_local : x_local + w]
        
        if alarm_cell_frame.size == 0:
            return ""
            
        return self._ocr_single_cell(alarm_cell_frame)

    def _parse_time_str(self, time_str: str) -> Optional[int]:
        """Parse time string like '56:28', '10:48', '02.14' into total seconds."""
        s = time_str.strip().lower()
        # Replace common OCR misreads for numbers
        s = s.replace('o', '0').replace('i', '1').replace('l', '1').replace('b', '8')
        digits = re.findall(r'\d+', s)
        if not digits:
            return None
        
        try:
            if len(digits) == 3:
                # HH:MM:SS
                return int(digits[0]) * 3600 + int(digits[1]) * 60 + int(digits[2])
            elif len(digits) == 2:
                # MM:SS
                return int(digits[0]) * 60 + int(digits[1])
            elif len(digits) == 1:
                val = digits[0]
                if len(val) >= 3:
                    # E.g. "5628" -> 56 mins, 28 secs
                    return int(val[:-2]) * 60 + int(val[-2:])
                else:
                    # Just minutes
                    return int(val) * 60
        except ValueError:
            return None
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _clear_target(self) -> None:
        self._target = None

    def _safe_capture(self) -> Optional[np.ndarray]:
        try:
            return self._capture.capture() if self._capture else None
        except Exception:
            return None

    def _notify(self, ocr_text: Optional[str] = None) -> None:
        """Push a state update to the StatusWindow (if wired up)."""
        if not self._on_update:
            return
        try:
            self._on_update(
                state=self._state.name,
                automation_on=self._enabled,
                paused=self._paused,
                fps=self._fps,
                ocr_text=ocr_text,
                last_event=self._state.name,
            )
        except Exception:
            pass  # never let UI errors kill the automation

    def _update_fps(self, elapsed: float) -> None:
        self._loop_times.append(elapsed)
        if len(self._loop_times) > 30:
            self._loop_times.pop(0)
        avg = sum(self._loop_times) / len(self._loop_times)
        self._fps = 1.0 / avg if avg > 0 else 0.0
        # Push FPS update every 30 loops
        if len(self._loop_times) % 10 == 0 and self._on_update:
            try:
                self._on_update(fps=self._fps)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Initialisation / teardown
    # ------------------------------------------------------------------

    def _init_subsystems(self) -> None:
        cfg = self._cfg
        self._capture = ScreenCapture(
            table_region=cfg.table_region,
            monitor_index=cfg.get("monitor_index", 1),
        )
        self._ocr = OCRReader(cfg.ocr_cfg)
        self._detector = ClaimDetector(
            row_detection_cfg=cfg.row_detection,
            table_region=cfg.table_region,
            select_column=cfg.select_column,
            claim_column=cfg.claim_column,
            connect_button=cfg.connect_button,
        )
        self._clicker = MouseController(cfg.mouse_cfg, cfg.timing)
        self._log.info("All sub-systems initialised.")
        self._notify()

    def _shutdown(self) -> None:
        if self._capture:
            self._capture.close()
        self._log.info("WorkflowEngine shut down cleanly.")
        self._log.shutdown()
