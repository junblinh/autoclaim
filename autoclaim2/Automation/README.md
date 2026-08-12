# Teleops GUI Automation Tool

A standalone Python desktop automation tool that monitors the **Teleops GUI** application and automatically claims available bot rows — using only screen capture, OCR, and mouse control. No source code access, no DLL injection, no reverse engineering.

---

## Features

| Feature | Details |
|---------|---------|
| **Screen capture** | MSS partial-screen capture, < 10 ms per frame |
| **OCR** | EasyOCR on CLAIM column only, < 70 ms |
| **Row detection** | OpenCV horizontal projection + colour-change fallback |
| **FSM** | 10-state finite state machine with validated transitions |
| **Safety** | Debounce, click TTL, FAILSAFE — no spam clicks |
| **Debug overlay** | Live HUD: rows, OCR boxes, state, FPS |
| **Logging** | Rotating text log + JSON Lines event log |
| **Calibration** | Drag-to-select GUI overlay for region setup |

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** EasyOCR will download its model (~100 MB) on first run. This is cached in `~/.EasyOCR`.

### 2. (Optional) GPU acceleration

If you have an NVIDIA GPU:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

Then set `"gpu": true` in `config.json` under `"ocr"`.

### 3. Set your username

Open `config.json` and change:

```json
"username": "YOUR_USERNAME_HERE"
```

to your actual Teleops username (the text that appears in the CLAIM cell after a successful claim).

### 4. Calibrate screen regions

```bash
python main.py calibrate
```

A semi-transparent overlay opens. Follow the on-screen instructions to drag-select:
1. The **table region** (the entire bot list)
2. The **CLAIM column** (the column showing "CLAIM" text)
3. The **SELECT column** (the column with Select buttons)
4. **Click** the Connect button

Results are saved to `config.json` automatically.

### 5. Verify setup

```bash
python main.py status
```

### 6. Run the automation

```bash
python main.py run
```

With debug overlay:

```bash
python main.py run --debug
# or
python main.py debug
```

---

## Project Structure

```
automation/
├── __init__.py
├── config.py        # ConfigManager — loads/saves config.json
├── capture.py       # ScreenCapture — MSS-based region capture
├── ocr.py           # OCRReader — EasyOCR CLAIM column reader
├── vision.py        # ClaimDetector — row detection + button finding
├── clicker.py       # MouseController — safe debounced clicks
├── workflow.py      # WorkflowEngine — FSM automation loop
├── calibrate.py     # CalibrationTool — drag-to-select GUI setup
├── overlay.py       # DebugOverlay — live HUD window
├── logger.py        # Logger — text + JSONL structured logs
├── templates/       # Optional: select_btn.png for template matching
├── logs/            # Auto-created: daily rotating logs
└── screenshots/     # Auto-created: error screenshots
main.py              # CLI entry point
config.json          # Runtime configuration
requirements.txt
README.md
```

---

## Configuration Reference (`config.json`)

| Key | Description | Default |
|-----|-------------|---------|
| `monitor_index` | MSS monitor index (1 = primary) | `1` |
| `table_region` | Absolute screen rect of the bot table | calibrated |
| `claim_column` | Absolute screen rect of the CLAIM column | calibrated |
| `select_column` | Absolute screen rect of the SELECT column | calibrated |
| `connect_button` | Absolute screen position of Connect button | calibrated |
| `username` | Your Teleops username | **required** |
| `ocr.gpu` | Use GPU for EasyOCR | `false` |
| `ocr.confidence_threshold` | Minimum OCR confidence (0–1) | `0.4` |
| `ocr.claim_keyword` | Text to match in CLAIM cell | `"CLAIM"` |
| `timing.loop_interval_ms` | Main loop interval | `100` |
| `timing.claim_timeout_s` | Max wait for claim confirmation | `5` |
| `timing.debounce_ms` | Min time between any two clicks | `500` |
| `debug.enabled` | Show debug overlay | `false` |
| `row_detection.use_template_matching` | Use template image for Select btn | `false` |

---

## Finite State Machine

```
IDLE
 │
 ▼
SEARCHING ──── no CLAIM ──────────────────────────────── loop
 │ CLAIM found
 ▼
CLAIM_FOUND
 │
 ▼
CLICK_SELECT ─── click Select button
 │
 ▼
WAIT_CLAIM ──── poll OCR for username ── timeout ──► TIMEOUT ──► ERROR ──► SEARCHING
 │ username confirmed
 ▼
CLAIM_SUCCESS
 │
 ▼
CLICK_CONNECT ─── click Connect button
 │
 ▼
CONNECTED ──── wait 1s ──► SEARCHING
```

---

## Safety Features

- **No double-click:** Click history with configurable TTL prevents re-clicking the same logical target.
- **No premature Connect:** The FSM transition matrix makes it architecturally impossible to reach `CLICK_CONNECT` without passing through `CLAIM_SUCCESS`.
- **Timeout recovery:** If OCR doesn't confirm a claim within `claim_timeout_s`, the engine logs a TIMEOUT and resumes scanning.
- **Auto-recovery from errors:** The ERROR state automatically resets all state and resumes scanning after 2 seconds.
- **pyautogui FAILSAFE:** Move the mouse to the **top-left corner** of the screen to immediately abort the automation.
- **Ctrl-C:** Standard interrupt also cleanly shuts down.

---

## Template Matching (optional)

For higher Select button detection accuracy, screenshot the Select button and save it as:

```
automation/templates/select_btn.png
```

Then enable in `config.json`:

```json
"row_detection": {
    "use_template_matching": true
}
```

---

## Logs

| File | Contents |
|------|----------|
| `automation/logs/automation_YYYYMMDD.log` | Human-readable timestamped events |
| `automation/logs/events_YYYYMMDD.jsonl` | Machine-readable JSON Lines (one event per line) |
| `automation/screenshots/` | PNG snapshots saved on errors |

---

## Troubleshooting

| Problem | Solution |
|---------|---------|
| OCR misses CLAIM rows | Lower `ocr.confidence_threshold` (try `0.25`) |
| Select button not found | Enable `use_template_matching` and provide `select_btn.png` |
| Too slow / high CPU | Increase `loop_interval_ms` to `200` |
| Calibration GUI won't open | Run `python main.py calibrate` from the normal Windows desktop (not SSH/headless) |
| Claims stolen before click | Decrease `loop_interval_ms` to `50` |
| Wrong row clicked | Recalibrate — ensure SELECT column rect aligns precisely |

---

## License

MIT — use freely for personal automation tasks.
