"""
hotkeys.py — Global Hotkey Manager (Windows Native)
Registers system-wide hotkeys using Win32 RegisterHotKey to avoid keylogger flags.

Hotkeys are read from config.json under the "hotkeys" key.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading
from typing import Callable, Optional

from .workflow import WorkflowEngine

# Win32 Constants
WM_HOTKEY = 0x0312

_VK_MAP: dict[str, int] = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "enter": 0x0D,
}

def _resolve_key(name: str) -> Optional[int]:
    if not name:
        return None
    return _VK_MAP.get(name.lower().strip())


class HotkeyThread(threading.Thread):
    def __init__(self, hotkeys: dict[int, tuple[int, Callable]]) -> None:
        super().__init__()
        self.hotkeys = hotkeys  # dict of: id -> (vk, callback)
        self.daemon = True
        self.running = True
        self._user32 = ctypes.windll.user32

    def run(self) -> None:
        # Register all hotkeys in this thread's context
        for hk_id, (vk, _) in self.hotkeys.items():
            # fsModifiers = 0 (no modifiers like Alt, Ctrl, Shift)
            res = self._user32.RegisterHotKey(None, hk_id, 0, vk)
            if not res:
                print(f"[ERROR] Failed to register hotkey ID {hk_id} (VK: {hex(vk)})")

        try:
            msg = ctypes.wintypes.MSG()
            while self.running:
                # GetMessageW blocks until a message is received
                if self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                    if msg.message == WM_HOTKEY:
                        hk_id = msg.wParam
                        if hk_id in self.hotkeys:
                            _, callback = self.hotkeys[hk_id]
                            try:
                                callback()
                            except Exception:
                                pass
                    self._user32.TranslateMessage(ctypes.byref(msg))
                    self._user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            # Clean up unregistered hotkeys
            for hk_id in self.hotkeys.keys():
                self._user32.UnregisterHotKey(None, hk_id)

    def stop(self) -> None:
        self.running = False
        # Post a dummy message to break the GetMessageW block
        self._user32.PostThreadMessageW(self.ident, 0, 0, 0)


class HotkeyManager:
    def __init__(
        self,
        hotkeys_cfg: dict,
        engine: WorkflowEngine,
    ) -> None:
        self._cfg = hotkeys_cfg or {}
        self._engine = engine
        self._on_toggle = engine.toggle
        self._on_emergency_stop = engine.emergency_stop
        self._on_pause_resume = engine.pause_resume
        self._on_exit = engine.safe_exit

        # Resolve configured key names to Virtual Key codes
        self._vk_toggle = _resolve_key(self._cfg.get("toggle", "f8"))
        self._vk_stop = _resolve_key(self._cfg.get("emergency_stop", "f9"))
        self._vk_pause = _resolve_key(self._cfg.get("pause_resume", "f10"))
        self._vk_exit = _resolve_key(self._cfg.get("exit", "esc"))

        self._thread: Optional[HotkeyThread] = None

    def start(self) -> None:
        """Start the Win32 hotkey thread."""
        hotkeys_to_register = {}
        
        # Build mapping of ID -> (VK, Callback)
        # Using simple numeric IDs for registration
        id_counter = 1
        if self._vk_toggle and self._on_toggle:
            hotkeys_to_register[id_counter] = (self._vk_toggle, self._on_toggle)
            id_counter += 1
        if self._vk_stop and self._on_emergency_stop:
            hotkeys_to_register[id_counter] = (self._vk_stop, self._on_emergency_stop)
            id_counter += 1
        if self._vk_pause and self._on_pause_resume:
            hotkeys_to_register[id_counter] = (self._vk_pause, self._on_pause_resume)
            id_counter += 1
        if self._vk_exit and self._on_exit:
            hotkeys_to_register[id_counter] = (self._vk_exit, self._on_exit)
            id_counter += 1

        if not hotkeys_to_register:
            return

        self._thread = HotkeyThread(hotkeys_to_register)
        self._thread.start()

    def stop(self) -> None:
        """Stop the hotkey thread and unregister hotkeys."""
        if self._thread:
            self._thread.stop()
            self._thread.join(timeout=1.0)
            self._thread = None
