import json
import copy
import os

base_dir = r"c:\Users\SYMBOTIC\Documents\Symbotic_Teleops\autoclaim2\Automation"

with open(os.path.join(base_dir, 'config.json'), 'r') as f:
    cfg = json.load(f)

# Config 1
cfg1 = copy.deepcopy(cfg)
cfg1["window_title"] = "Teleops Auto (Monitor 1)"
cfg1["monitor_index"] = 1
with open(os.path.join(base_dir, 'config_1.json'), 'w') as f:
    json.dump(cfg1, f, indent=2)

# Config 2
cfg2 = copy.deepcopy(cfg)
cfg2["window_title"] = "Teleops Auto (Monitor 2)"
cfg2["monitor_index"] = 2
cfg2["hotkeys"] = {
    "toggle": "f5",
    "pause_resume": "f7",
    "emergency_stop": "f6"
}
with open(os.path.join(base_dir, 'config_2.json'), 'w') as f:
    json.dump(cfg2, f, indent=2)

# VBS 1
vbs1_path = r"c:\Users\SYMBOTIC\Documents\Symbotic_Teleops\autoclaim2\Start Monitor 1.vbs"
with open(vbs1_path, 'w') as f:
    f.write('Set oShell = CreateObject("WScript.Shell")\n')
    f.write('oShell.CurrentDirectory = "c:\\Users\\SYMBOTIC\\Documents\\Symbotic_Teleops\\autoclaim2\\Automation"\n')
    f.write('oShell.Run "pythonw.exe main.py run --config config_1.json", 0, False\n')

# VBS 2
vbs2_path = r"c:\Users\SYMBOTIC\Documents\Symbotic_Teleops\autoclaim2\Start Monitor 2.vbs"
with open(vbs2_path, 'w') as f:
    f.write('Set oShell = CreateObject("WScript.Shell")\n')
    f.write('oShell.CurrentDirectory = "c:\\Users\\SYMBOTIC\\Documents\\Symbotic_Teleops\\autoclaim2\\Automation"\n')
    f.write('oShell.Run "pythonw.exe main.py run --config config_2.json", 0, False\n')
