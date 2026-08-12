import os
import time
from PIL import ImageGrab
import automation.gui as gui

def capture():
    # Setup dummy data
    hotkeys = {"toggle": "F8", "pause_resume": "F10", "emergency_stop": "F9"}
    blocklist = ["SiteA", "SiteB", "TestSite"]
    
    sw = gui.StatusWindow(hotkeys=hotkeys, blocklist=blocklist)
    
    # Update state to look active
    sw.update(state="SCANNING", automation_on=True, fps=31.4, ocr_text="")
    
    sw.root.update_idletasks()
    sw.root.update()
    
    time.sleep(1)
    sw.root.update()
    
    x = sw.root.winfo_rootx()
    y = sw.root.winfo_rooty()
    w = sw.root.winfo_width()
    h = sw.root.winfo_height()
    
    try:
        img = ImageGrab.grab(bbox=(x, y, x+w, y+h), all_screens=True)
    except:
        img = ImageGrab.grab(bbox=(x, y, x+w, y+h))
    
    # Save to artifacts
    out_dir = r"C:\Users\SYMBOTIC\.gemini\antigravity-ide\brain\62ef3ece-1b04-4f4b-9e52-8db15eca2877"
    img.save(os.path.join(out_dir, "ui_preview.png"))
    
    sw.root.destroy()

if __name__ == "__main__":
    capture()
