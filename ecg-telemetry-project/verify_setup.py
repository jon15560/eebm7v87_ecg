#!/usr/bin/env python3
"""
verify_setup.py  --  check the machine is ready before you start.

Run this first. It checks each thing the project needs and tells you exactly
what to do about anything missing, so problems surface now rather than halfway
through a demo.

    python3 verify_setup.py
"""

import importlib
import os
import shutil
import socket
import subprocess
import sys

OK, WARN, BAD = "  [ok]  ", "  [--]  ", "  [XX]  "
problems, warnings = [], []


def check(label, ok, fix=None, fatal=True):
    if ok:
        print(OK + label)
    else:
        print((BAD if fatal else WARN) + label)
        if fix:
            print("         -> " + fix.replace("\n", "\n         "))
        (problems if fatal else warnings).append(label)
    return ok


print("Checking your setup\n" + "-" * 60)

# --- Python --------------------------------------------------------------
v = sys.version_info
check("Python %d.%d" % (v.major, v.minor), v >= (3, 9),
      "Install Python 3.9 or newer from python.org.\n"
      "On Windows, tick 'Add python.exe to PATH' during install.")

# --- packages -------------------------------------------------------------
for mod, why, fatal in (("numpy", "maths", True),
                        ("scipy", "resampling", True),
                        ("wfdb", "downloading ECG records", True),
                        ("flask", "the server", True),
                        ("sklearn", "training the classifier", False),
                        ("matplotlib", "report figures", False),
                        ("pandas", "report figures", False)):
    try:
        importlib.import_module(mod)
        print(OK + "%s (%s)" % (mod, why))
    except ImportError:
        name = "scikit-learn" if mod == "sklearn" else mod
        check("%s (%s)" % (mod, why), False,
              "pip install " + name, fatal=fatal)

# --- virtualenv -----------------------------------------------------------
in_venv = hasattr(sys, "real_prefix") or sys.base_prefix != sys.prefix
if not in_venv:
    print(WARN + "not running inside a virtual environment")
    print("         -> fine if you installed packages globally or use Anaconda,")
    print("            but a venv keeps this project's packages separate.")

# --- java -----------------------------------------------------------------
javac = shutil.which("javac")
check("javac (needed only for tools/cross_check.py)", javac is not None,
      "Install a JDK:  winget install Microsoft.OpenJDK.21\n"
      "Then open a NEW terminal so PATH updates.", fatal=False)

# --- internet / PhysioNet --------------------------------------------------
try:
    socket.create_connection(("physionet.org", 443), timeout=8).close()
    print(OK + "can reach physionet.org")
except Exception as e:
    check("can reach physionet.org", False,
          "No internet, or a firewall is blocking it. The ECG records are\n"
          "downloaded live, so this must work. Try a phone hotspot.")

# --- project files ---------------------------------------------------------
here = os.path.dirname(os.path.abspath(__file__))
for rel in ("server/ecg_algorithms.py", "server/server.py",
            "transmitter/ecg_transmitter.py", "tools/cross_check.py"):
    check("found %s" % rel, os.path.exists(os.path.join(here, rel)),
          "You may be running this from the wrong folder, or the\n"
          "download was incomplete. Re-extract the project zip.")

# --- LAN address -----------------------------------------------------------
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
    s.close()
    print(OK + "this machine's network address is %s" % ip)
    print("         -> the phone will send data to http://%s:8000/api/ecg" % ip)
    print("            Write that down; you will need it twice.")
except Exception:
    print(WARN + "could not work out this machine's network address")

# --- summary ---------------------------------------------------------------
print("-" * 60)
if problems:
    print("\n%d problem(s) must be fixed before continuing:" % len(problems))
    for p in problems:
        print("   - " + p)
    sys.exit(1)

if warnings:
    print("\nEverything essential is present. Optional items missing:")
    for w in warnings:
        print("   - " + w)
    print("\nYou can start. Install the optional items when you reach the")
    print("steps that need them (training the classifier, and the report).")
else:
    print("\nEverything is ready. Start with STEP 1 in START_HERE.md.")
