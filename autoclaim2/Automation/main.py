"""
main.py — Teleops GUI Automation Tool (Terminal Mode)
Chạy trực tiếp từ terminal, không cần GUI/System Tray.

Cách dùng:
    python main.py              # Chạy automation (F8: bật/tắt, F9: dừng, F10: pause, ESC: thoát)
    python main.py calibrate    # Hiệu chỉnh vùng màn hình (CLI)
    python main.py status       # Xem trạng thái config hiện tại
"""

import sys
import signal
import threading
import time
import argparse

# Force stdout to UTF-8 to prevent UnicodeEncodeError with emojis on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from automation.config import ConfigManager
from automation.logger import Logger
from automation.workflow import WorkflowEngine
from automation.hotkeys import HotkeyManager
from automation.gui import StatusWindow


# ── Terminal status printer ──────────────────────────────────────────────────

class TerminalStatus:
    """In trạng thái automation ra terminal theo thời gian thực."""

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

    def __init__(self):
        self._last_state = ""

    def update(
        self,
        state=None,
        automation_on=None,
        paused=None,
        fps=None,
        ocr_text=None,
        last_event=None,
    ):
        if state is None:
            return
        if state == self._last_state:
            return
        self._last_state = state

        icon = self._ICONS.get(state, "•")
        ts = time.strftime("%H:%M:%S")
        auto_str = ""
        if automation_on is not None:
            auto_str = "  [AUTO:ON]" if automation_on else "  [AUTO:OFF]"
        fps_str = f"  {fps:.1f}fps" if fps is not None else ""
        print(f"[{ts}] {icon}  {state}{auto_str}{fps_str}", flush=True)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Teleops GUI Automation Tool — Terminal Mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Hotkeys (hoạt động toàn hệ thống):
  F8   — Bật / Tắt automation
  F9   — Dừng khẩn cấp
  F10  — Tạm dừng / Tiếp tục
  ESC  — Thoát chương trình

Ví dụ:
  python main.py              # Chạy automation
  python main.py calibrate    # Hiệu chỉnh màn hình (CLI)
  python main.py status       # Xem trạng thái config
        """
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "calibrate", "status"],
        help="Lệnh cần chạy (mặc định: run)",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Đường dẫn đến file config (mặc định: config.json)",
    )
    parser.add_argument(
        "--monitor",
        type=int,
        default=None,
        help="Ghi đè monitor_index cho calibration",
    )
    args = parser.parse_args()

    cfg = ConfigManager(args.config)
    log = Logger(cfg.logging_cfg)

    # ── calibrate ──────────────────────────────────────────────────────
    if args.command == "calibrate":
        if args.monitor is not None:
            cfg.set("monitor_index", args.monitor)
            cfg.save()
            
        print("\n[+] Đang mở công cụ Calibration...")
        from automation.calibrate import CalibrationTool
        log.info("Bắt đầu hiệu chỉnh CLI...")
        calibrator = CalibrationTool(cfg, log)
        calibrator.run()
        log.info("Hiệu chỉnh xong.")
        return

    # ── status ─────────────────────────────────────────────────────────
    if args.command == "status":
        print("\n=== Teleops Automation — Config Status ===")
        print(f"  Config file      : {cfg._path}")
        print(f"  Calibrated       : {'YES' if cfg.is_calibrated() else 'NO  ← run: python main.py calibrate'}")
        print(f"  Username         : {cfg.username or '(chưa đặt)'}")
        print(f"  Table region     : {cfg.table_region}")
        print(f"  Type column      : {cfg.type_column}")
        print(f"  Site column      : {cfg.site_column}")
        print(f"  Alarm column     : {cfg.alarm_column}")
        print(f"  Claim column     : {cfg.claim_column}")
        print(f"  Select col       : {cfg.select_column}")
        print(f"  Connect btn      : {cfg.connect_button}")
        print(f"  Site blocklist   : {cfg.site_blocklist}")
        print(f"  Allowed Types    : {cfg.allowed_teleop_types}")
        print(f"  Max Alarm Time   : {cfg.max_alarm_time}")
        print(f"  Header exclusion : {cfg.get('header_exclusion_px', 5)} px")
        return

    # ── run ────────────────────────────────────────────────────────────
    if not cfg.is_calibrated():
        print("\n⚠️  Chưa hiệu chỉnh! Hãy chạy trước:")
        print("     python main.py calibrate\n")
        sys.exit(1)

    def on_gui_close():
        engine.safe_exit()
        hk_manager.stop()
        
    status_window = StatusWindow(cfg=cfg, on_close=on_gui_close)
    engine = WorkflowEngine(cfg, log, on_update=status_window.update)

    # Khởi động engine trong thread nền
    engine_thread = threading.Thread(target=engine.start, daemon=True, name="WorkflowEngine")
    engine_thread.start()

    # Đăng ký hotkeys toàn hệ thống
    hk_manager = HotkeyManager(cfg.get("hotkeys", {}), engine)
    hk_manager.start()

    print("\n" + "="*50)
    print("  🤖 Teleops Auto Claim — Terminal Mode")
    print("="*50)
    print(f"  User      : {cfg.username or '(chưa đặt)'}")
    print(f"  Blocklist : {cfg.site_blocklist or 'Không có'}")
    print()
    print("  Hotkeys:")
    hk = cfg.get("hotkeys", {})
    print(f"    {hk.get('toggle', 'F8').upper():6} — Bật / Tắt automation")
    print(f"    {hk.get('emergency_stop', 'F9').upper():6} — Dừng khẩn cấp")
    print(f"    {hk.get('pause_resume', 'F10').upper():6} — Tạm dừng / Tiếp tục")
    print(f"    {'ESC':6} — Thoát chương trình")
    print()
    print("  Nhấn Ctrl+C hoặc ESC để thoát.")
    print("="*50 + "\n")

    # Xử lý thoát sạch khi Ctrl+C
    def _sigint(sig, frame):
        print("\n[!] Ctrl+C nhận được — đang thoát...")
        engine.safe_exit()
        hk_manager.stop()
        status_window.root.quit()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigint)

    # Start the GUI event loop on the main thread
    try:
        status_window.run()
    except KeyboardInterrupt:
        pass
    finally:
        engine.safe_exit()
        hk_manager.stop()

    print("[✓] Đã thoát.")


if __name__ == "__main__":
    main()
