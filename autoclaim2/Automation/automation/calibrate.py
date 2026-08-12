"""
calibrate.py — CalibrationTool
Interactive tool to define all screen regions and button positions.

Workflow:
  1. Open a full-screen semi-transparent overlay window.
  2. User drags rectangles to define: table_region, claim_column, select_column.
  3. User clicks once to define: connect_button position.
  4. Results are written to config.json.

Fallback (no GUI): coordinate entry via CLI prompts.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import Optional

from .config import ConfigManager
from .logger import Logger


# ---------------------------------------------------------------------------
# Overlay window for drag-to-select calibration
# ---------------------------------------------------------------------------

class _CalibrationOverlay(tk.Toplevel):
    """
    Semi-transparent full-screen window for region selection.
    Draws selection rectangles with green outlines.
    """

    INSTRUCTIONS = {
        "table_region":    "Step 1/7 — DRAG to select the TABLE region (the entire bot list area).",
        "type_column":     "Step 2/7 — DRAG to select the TELEOP TYPE COLUMN (showing SUSPECT, etc.).",
        "site_column":     "Step 3/7 — DRAG to select the SITE COLUMN (the column with site names).",
        "alarm_column":    "Step 4/7 — DRAG to select the ALARMED FOR COLUMN (showing MM:SS time).",
        "claim_column":    "Step 5/7 — DRAG to select the CLAIM COLUMN (the column showing 'CLAIM' text).",
        "select_column":   "Step 6/7 — DRAG to select the SELECT COLUMN (the column with the Select buttons).",
        "connect_button":  "Step 7/7 — DRAG to select the CONNECT BUTTON.",
    }

    STEPS = ["table_region", "type_column", "site_column", "alarm_column", "claim_column", "select_column", "connect_button"]

    def __init__(self, master=None, monitor_index=1) -> None:
        if master is None:
            # Fallback if run without a root window
            self._temp_root = tk.Tk()
            self._temp_root.withdraw()
            master = self._temp_root
        else:
            self._temp_root = None
            
        super().__init__(master)

        self.results: dict = {}
        self._step_index: int = 0
        self._drag_start: Optional[tuple[int, int]] = None
        self._current_rect_id: Optional[int] = None

        import mss
        with mss.mss() as sct:
            if monitor_index < len(sct.monitors):
                mon = sct.monitors[monitor_index]
            else:
                mon = sct.monitors[1]
                
        w, h = mon["width"], mon["height"]
        x, y = mon["left"], mon["top"]
        x_str = f"+{x}"
        y_str = f"+{y}"
        
        self.geometry(f"{w}x{h}{x_str}{y_str}")
        self.overrideredirect(True)
        self.attributes("-alpha", 0.35)
        self.configure(bg="black")
        self.attributes("-topmost", True)

        # Canvas fills whole screen
        self._canvas = tk.Canvas(self, bg="black", highlightthickness=0, cursor="crosshair")
        self._canvas.pack(fill="both", expand=True)

        # Instruction label
        self._label = tk.Label(
            self,
            text="",
            font=("Segoe UI", 18, "bold"),
            fg="lime",
            bg="#111111",
            padx=20,
            pady=10,
        )
        # Move it to the bottom center so it doesn't block the Connect button
        self._label.place(relx=0.5, rely=0.95, anchor="center")
        
        # Pass clicks through the label down to the canvas
        self._label.bind("<ButtonPress-1>", self._on_press)
        self._label.bind("<B1-Motion>", self._on_drag)
        self._label.bind("<ButtonRelease-1>", self._on_release)

        self._bind_events()
        self._set_step(0)

    def _bind_events(self) -> None:
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Escape>", lambda _: self.destroy())

    def _set_step(self, index: int) -> None:
        self._step_index = index
        if index >= len(self.STEPS):
            self.destroy()
            if getattr(self, "_temp_root", None):
                self._temp_root.destroy()
            return
        step = self.STEPS[index]
        self._label.config(text=self.INSTRUCTIONS[step])
        self._canvas.delete("selection")

    def _get_coords(self, event: tk.Event) -> tuple[int, int]:
        """Convert root coordinates to canvas coordinates to handle clicks anywhere."""
        return event.x_root - self.winfo_rootx(), event.y_root - self.winfo_rooty()

    def _on_press(self, event: tk.Event) -> None:
        self._drag_start = self._get_coords(event)

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag_start is None:
            return
        x0, y0 = self._drag_start
        x1, y1 = self._get_coords(event)
        self._canvas.delete("selection")
        self._current_rect_id = self._canvas.create_rectangle(
            x0, y0, x1, y1,
            outline="lime", width=2, tags="selection",
        )

    def _on_release(self, event: tk.Event) -> None:
        if self._drag_start is None:
            return
        x0, y0 = self._drag_start
        x1, y1 = self._get_coords(event)
        self._drag_start = None

        # Absolute screen coordinates
        left = min(x0, x1) + self.winfo_rootx()
        top = min(y0, y1) + self.winfo_rooty()
        width = abs(x1 - x0)
        height = abs(y1 - y0)

        if width < 5 or height < 5:
            return  # ignore accidental tiny drags

        step = self.STEPS[self._step_index]
        self.results[step] = {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        }
        self.after(400, lambda: self._set_step(self._step_index + 1))


# ---------------------------------------------------------------------------
# CLI fallback calibration
# ---------------------------------------------------------------------------

def _calibrate_cli(cfg: ConfigManager) -> None:
    """Prompt user for coordinates via CLI when GUI is unavailable."""
    print("\n=== Teleops Calibration (CLI mode) ===")
    print("Enter screen coordinates in pixels (integers).")
    print("Tip: use a screen ruler tool or Windows Snipping Tool to find coords.\n")

    def ask_rect(name: str) -> dict:
        print(f"--- {name} ---")
        left   = int(input("  left   (x): "))
        top    = int(input("  top    (y): "))
        width  = int(input("  width     : "))
        height = int(input("  height    : "))
        return {"left": left, "top": top, "width": width, "height": height}

    def ask_point(name: str) -> dict:
        print(f"--- {name} ---")
        x = int(input("  x: "))
        y = int(input("  y: "))
        return {"x": x, "y": y}

    cfg.set("table_region",   ask_rect("Table Region"))
    cfg.set("type_column",    ask_rect("TELEOP TYPE Column"))
    cfg.set("site_column",    ask_rect("SITE Column"))
    cfg.set("alarm_column",   ask_rect("ALARMED FOR Column"))
    cfg.set("claim_column",   ask_rect("CLAIM Column"))
    cfg.set("select_column",  ask_rect("SELECT Column"))
    cfg.set("connect_button", ask_rect("CONNECT Button"))

    username = input("\nEnter your username (will appear in CLAIM cell): ").strip()
    if username:
        cfg.set("username", username)

    cfg.save()
    print(f"\n✓ Configuration saved to config.json")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class CalibrationTool:
    """
    Runs interactive calibration (GUI preferred, CLI fallback).

    Usage::

        tool = CalibrationTool(cfg, logger)
        tool.run()
    """

    def __init__(self, cfg: ConfigManager, logger: Logger) -> None:
        self._cfg = cfg
        self._log = logger

    def run(self) -> None:
        """Run calibration. Saves results to config.json."""
        self._log.info("Starting calibration...")

        # Prompt for username regardless of method
        username_set = bool(self._cfg.username and self._cfg.username != "YOUR_USERNAME_HERE")

        try:
            self._run_gui()
        except Exception as exc:
            self._log.warning(f"GUI calibration failed ({exc}) — falling back to CLI.")
            _calibrate_cli(self._cfg)
            return

        # GUI succeeded — now prompt for username if needed
        if not username_set:
            self._prompt_username_cli()

        self._cfg.save()
        warnings = self._cfg.validate()
        if warnings:
            for w in warnings:
                self._log.warning(f"Config warning: {w}")
        else:
            self._log.info("Calibration complete — all regions configured.")

    def _run_gui(self, master: Optional[tk.Tk] = None) -> None:
        """Launch overlay GUI and block until done."""
        mon_idx = self._cfg.get("monitor_index", 1)
        overlay = _CalibrationOverlay(master, monitor_index=mon_idx)
        
        # Block until the overlay is destroyed
        if master:
            master.wait_window(overlay)
        else:
            overlay.mainloop()

        if not overlay.results:
            raise RuntimeError("No regions were selected.")

        for key, val in overlay.results.items():
            self._cfg.set(key, val)

    def _prompt_username_cli(self) -> None:
        """Ask for username via simple console input."""
        print()
        username = input("Enter your Teleops username (shown in CLAIM cell after claiming): ").strip()
        if username:
            self._cfg.set("username", username)
