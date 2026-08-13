#!/usr/bin/env bash
# setup_pi.sh -- one-shot setup for the Raspberry Pi ECG transmitter.
#
#   chmod +x setup_pi.sh && ./setup_pi.sh
#
# Installs system packages, creates a virtualenv, and installs the Python
# dependencies. Safe to re-run.
set -e

echo "== checking Bluetooth adapter =="
if ! hciconfig hci0 > /dev/null 2>&1; then
    echo "!! No hci0 adapter found. On a Pi 4 this usually means Bluetooth is"
    echo "   soft-blocked. Try:  sudo rfkill unblock bluetooth"
    exit 1
fi
sudo hciconfig hci0 up || true
hciconfig hci0 | head -3

echo
echo "== installing system packages =="
# libbluetooth-dev MUST be present before pybluez2 is built, or the pip
# install fails with an unhelpful compiler error about missing headers.
sudo apt-get update -qq
sudo apt-get install -y python3-venv python3-dev libbluetooth-dev bluez

echo
echo "== creating virtualenv =="
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip -q

echo
echo "== installing Python packages (scipy compiles slowly on ARM; be patient) =="
pip install -r requirements-pi.txt

echo
echo "== done =="
python3 - <<'PY'
try:
    import bluetooth
    print("pybluez2 OK -- the transmitter will advertise SPP automatically.")
except ImportError:
    print("pybluez2 NOT installed. The transmitter still works, but you must")
    print("register the SPP record manually -- see README-PI.md, 'If pybluez2")
    print("failed'.")
PY
echo
echo "Next:"
echo "  source .venv/bin/activate"
echo "  python3 ecg_transmitter.py --record 119 --loop"
