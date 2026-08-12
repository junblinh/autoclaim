# AutoClaim for Symbotic Teleops

AutoClaim is a standalone desktop automation tool that monitors the Teleops GUI and automatically claims available bot rows. It uses fast screen capture and OCR to detect "CLAIM" statuses, and then safely performs mouse clicks to select and connect to the bots.

This repository contains everything needed to set up and run the tool, including convenient shortcut scripts for easy daily use.

---

## 🛠️ Environment Setup

To run AutoClaim, you need to set up the Python environment and install the required dependencies.

### 1. Prerequisites
- **Python 3.9+** must be installed on your machine.
- Ensure Python is added to your system `PATH`.

### 2. Install Dependencies
Open a terminal (Command Prompt or PowerShell) and run the following commands to install the required Python packages:

```cmd
cd Automation
pip install -r requirements.txt
```

*(Note: The first time you run the tool, EasyOCR will automatically download its required AI models. This may take a moment depending on your internet connection).*

### 3. Configure Your Username
Before running the bot, you need to tell it your Teleops username so it knows when a claim is successful. 
1. Open `Automation\config.json` in a text editor (like Notepad).
2. Find the `"username"` field and change `"YOUR_USERNAME_HERE"` to your actual Teleops username.

---

## 🚀 How to Use

The project includes several `.vbs` (VBScript) files in the root folder so you don't have to open a terminal to use the tool. Just double-click them!

### Step 1: Calibration
Before the tool can work, it needs to know where the Teleops table is on your screen. 
- Double-click **`Calibrate Monitor 1.vbs`** (or Monitor 2 if you run the GUI on a secondary screen).
- A prompt will ask you to confirm your monitor number.
- A semi-transparent overlay will appear. Follow the on-screen instructions to click-and-drag over the required regions (the whole table, the CLAIM column, and the Select column) and then click the Connect button. 
- This only needs to be done once, unless you move the Teleops window.

### Step 2: Start Automation
Once calibrated, you can start the automation tool:
- Double-click **`Start AutoClaim.vbs`** (or `Start Monitor 1/2.vbs` depending on your setup).
- The tool will run in the background (a Python window may appear or run silently) and begin scanning for available bots.

To stop the automation immediately in an emergency, move your mouse to the **top-left corner** of your screen (FailSafe), or close the terminal window.

---

## 📖 Advanced / Developer Documentation

If you want to understand the inner workings of the state machine, tweak advanced timings, or run the tool with the debug HUD overlay, please see the detailed developer documentation located here:

👉 [Automation/README.md](Automation/README.md)
