"""
gui.py — Tkinter Status Overlay
A small, always-on-top window to show live automation status.
"""

import tkinter as tk
from tkinter import ttk
import queue
import time
from typing import Callable, Optional
from automation.config import ConfigManager

KNOWN_SITES = [
    "BRH", "BRK", "BCK", "CNT", "CLM", "DOUG", "GRC", "HRM", "HPM", 
    "IRV", "LVL", "MID", "MNM", "MRC", "NBF", "OTW", "PAL", "PLV", 
    "PRT", "RYM", "RBL", "SRC", "STL", "SYM", "WDL"
]

class StatusWindow:
    _ICONS = {
        "IDLE":           "⬜",
        "SCANNING":       "🔍",
        "CLAIM_FOUND":    "🎯",
        "CLICK_CLAIM":    "🖱 ",
        "WAIT_USERNAME":  "⏳",
        "CLAIM_SUCCESS":  "✅",
        "CLICK_SELECT":   "🖱 ",
        "CLICK_CONNECT":  "🔗",
        "CONNECTED":      "🟢",
        "STOPPED":        "⏹ ",
        "ERROR":          "❌",
        "PAUSED":         "⏸ ",
    }

    def __init__(self, cfg: ConfigManager, on_close: Optional[Callable] = None):
        self._q = queue.Queue()
        self._on_close = on_close
        self.cfg = cfg

        self.root = tk.Tk()
        self.root.title(self.cfg.get("window_title", "Teleops Auto"))
        
        # Keep window always on top
        self.root.attributes("-topmost", True)
        self.root.geometry("280x480")
        self.root.resizable(False, False)
        
        # Handle close (X button)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_close)

        # Styling
        bg_color = "#1E1E1E"
        fg_color = "#FFFFFF"
        self.root.configure(bg=bg_color)

        self.status_label = tk.Label(
            self.root, 
            text="Initializing...", 
            font=("Segoe UI", 14, "bold"),
            bg=bg_color, 
            fg=fg_color
        )
        self.status_label.pack(pady=(15, 5))

        self.info_label = tk.Label(
            self.root, 
            text="[AUTO:OFF] | 0.0 fps", 
            font=("Consolas", 10),
            bg=bg_color, 
            fg="#AAAAAA"
        )
        self.info_label.pack(pady=(0, 5))

        self.details_label = tk.Label(
            self.root, 
            text="Press F8 to Start", 
            font=("Segoe UI", 9),
            bg=bg_color, 
            fg="#888888"
        )
        self.details_label.pack(pady=(0, 5))

        # Divider
        tk.Frame(self.root, height=1, bg="#444444").pack(fill="x", padx=20, pady=5)

        # Hotkeys Section
        hk_frame = tk.Frame(self.root, bg=bg_color)
        hk_frame.pack(fill="x", padx=20)
        
        tk.Label(hk_frame, text="HOTKEYS", font=("Segoe UI", 8, "bold"), bg=bg_color, fg="#55AADD").pack(anchor="w")
        
        hotkeys = self.cfg.get("hotkeys", {})
        t_key = hotkeys.get("toggle", "F8").upper()
        p_key = hotkeys.get("pause_resume", "F10").upper()
        e_key = hotkeys.get("emergency_stop", "F9").upper()
        
        tk.Label(hk_frame, text=f"{t_key}: Toggle Auto", font=("Segoe UI", 8), bg=bg_color, fg="#AAAAAA").pack(anchor="w")
        tk.Label(hk_frame, text=f"{p_key}: Pause/Resume", font=("Segoe UI", 8), bg=bg_color, fg="#AAAAAA").pack(anchor="w")
        tk.Label(hk_frame, text=f"{e_key}: Emergency Stop", font=("Segoe UI", 8), bg=bg_color, fg="#AAAAAA").pack(anchor="w")

        # Blocklist Section
        tk.Frame(self.root, height=1, bg="#444444").pack(fill="x", padx=20, pady=5)
        bl_frame = tk.Frame(self.root, bg=bg_color)
        bl_frame.pack(fill="x", padx=20, expand=True)
        tk.Label(bl_frame, text="BLOCKLIST (Select to toggle)", font=("Segoe UI", 8, "bold"), bg=bg_color, fg="#DD5555").pack(anchor="w")
        
        self.combo_var = tk.StringVar()
        self.combo = ttk.Combobox(bl_frame, textvariable=self.combo_var, state="readonly", values=["-- Choose site --"] + KNOWN_SITES)
        self.combo.current(0)
        self.combo.pack(fill="x", pady=(2, 5))
        
        self.blocked_sites_label = tk.Label(bl_frame, text="", font=("Segoe UI", 8), bg=bg_color, fg="#AAAAAA", wraplength=240, justify="left")
        self.blocked_sites_label.pack(anchor="w")
        
        self.combo.bind("<<ComboboxSelected>>", self._on_blocklist_change)
        self._refresh_blocklist_label()

        # Teleop Type Section
        tk.Frame(self.root, height=1, bg="#444444").pack(fill="x", padx=20, pady=5)
        type_frame = tk.Frame(self.root, bg=bg_color)
        type_frame.pack(fill="x", padx=20)
        tk.Label(type_frame, text="ALLOWED TYPES (Select to toggle)", font=("Segoe UI", 8, "bold"), bg=bg_color, fg="#55DD55").pack(anchor="w")
        
        self.type_combo_var = tk.StringVar()
        self.type_combo = ttk.Combobox(type_frame, textvariable=self.type_combo_var, state="readonly", values=["-- Choose type --", "SUSPECT", "CHRF", "DRIVING", "UNKNOWN", "CAS"])
        self.type_combo.current(0)
        self.type_combo.pack(fill="x", pady=(2, 5))
        
        self.allowed_types_label = tk.Label(type_frame, text="", font=("Segoe UI", 8), bg=bg_color, fg="#AAAAAA", wraplength=240, justify="left")
        self.allowed_types_label.pack(anchor="w")
        
        self.type_combo.bind("<<ComboboxSelected>>", self._on_type_change)
        self._refresh_type_label()

        # Schedule queue processing
        self.root.after(50, self._process_queue)
        
    def _refresh_blocklist_label(self):
        current_blocks = self.cfg.site_blocklist
        text = ", ".join(current_blocks) if current_blocks else "None"
        self.blocked_sites_label.config(text=f"Blocked: {text}")

    def _on_blocklist_change(self, event):
        """Save selected blocklist to config.json"""
        selected = self.combo_var.get()
        if selected == "-- Choose site --":
            return
            
        current_blocks = self.cfg.site_blocklist
        if selected in current_blocks:
            current_blocks.remove(selected)
        else:
            current_blocks.append(selected)
            
        self.cfg.set("site_blocklist", current_blocks)
        self.cfg.save()
        
        self._refresh_blocklist_label()
        self.combo.current(0) # reset combobox

    def _refresh_type_label(self):
        current_types = self.cfg.allowed_teleop_types
        text = ", ".join(current_types) if current_types else "None"
        self.allowed_types_label.config(text=f"Allowed: {text}")

    def _on_type_change(self, event):
        """Save selected types to config.json"""
        selected = self.type_combo_var.get()
        if selected == "-- Choose type --":
            return
            
        current_types = self.cfg.allowed_teleop_types
        if selected in current_types:
            current_types.remove(selected)
        else:
            current_types.append(selected)
            
        self.cfg.set("allowed_teleop_types", current_types)
        self.cfg.save()
        
        self._refresh_type_label()
        self.type_combo.current(0)

    def _handle_close(self):
        """Called when user closes the window."""
        if self._on_close:
            self._on_close()
        self.root.destroy()

    def update(
        self,
        state=None,
        automation_on=None,
        paused=None,
        fps=None,
        ocr_text=None,
        last_event=None,
    ):
        """Thread-safe update method called by WorkflowEngine."""
        self._q.put({
            "state": state,
            "automation_on": automation_on,
            "paused": paused,
            "fps": fps,
            "ocr_text": ocr_text,
        })

    def _process_queue(self):
        """Runs on main thread, processes UI updates."""
        try:
            while True:
                msg = self._q.get_nowait()
                self._update_ui(msg)
        except queue.Empty:
            pass
        finally:
            self.root.after(50, self._process_queue)

    def _update_ui(self, msg: dict):
        state = msg.get("state")
        automation_on = msg.get("automation_on")
        fps = msg.get("fps")
        ocr_text = msg.get("ocr_text")
        paused = msg.get("paused")

        if state:
            icon = self._ICONS.get(state, "•")
            self.status_label.config(text=f"{icon} {state}")

        info_parts = []
        if automation_on is not None:
            if paused:
                info_parts.append("[PAUSED]")
            else:
                info_parts.append("[AUTO:ON]" if automation_on else "[AUTO:OFF]")
        
        if fps is not None:
            info_parts.append(f"{fps:.1f} fps")
            
        if info_parts:
            self.info_label.config(text=" | ".join(info_parts))

        if ocr_text:
            # Show what the bot is reading
            self.details_label.config(text=f"Reading: '{ocr_text}'")
        elif state == "STOPPED":
            self.details_label.config(text="Press F8 to Start")
        elif state == "SCANNING":
            self.details_label.config(text="Scanning table...")

    def run(self):
        """Start the Tkinter event loop (blocks)."""
        self.root.mainloop()
