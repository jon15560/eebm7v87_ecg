# Raspberry Pi ECG transmitter

Three files. The Pi's only job is Part 1: stream a PhysioNet record over
Bluetooth SPP to the phone, standing in for a wearable sensor. It runs no
server and does no classification.

```
ecg_transmitter.py     the transmitter
requirements-pi.txt    Python dependencies
setup_pi.sh            one-shot installer
```

## 1. Copy these onto the Pi

From your laptop:

```powershell
scp -r pi-bundle pi@raspberrypi.local:~/ecg
```

Use whatever username you set when imaging the card. If `raspberrypi.local`
doesn't resolve, get the IP from your router and use that instead. A USB stick
works equally well.

## 2. Run the installer

SSH in, then:

```bash
cd ~/ecg
chmod +x setup_pi.sh
./setup_pi.sh
```

It checks the Bluetooth adapter, installs the system packages, creates a
virtualenv, and installs the Python dependencies. Expect several minutes —
scipy compiles slowly on ARM.

It ends by telling you whether `pybluez2` installed. That matters (see below).

## 3. Pair with the phone

Pairing is done in the phone's **Settings**, not in the app. The app only looks
at devices that are already paired; in-app discovery would need a scan dialog
and location permission and adds nothing here.

On the Pi:

```bash
bluetoothctl
```
then, inside it:
```
power on
agent on
default-agent
discoverable on
pairable on
```

Leave that running. On the phone: Settings → Bluetooth → find the Pi → pair.
Confirm the matching passkey on **both** screens. Then back in `bluetoothctl`:

```
trust XX:XX:XX:XX:XX:XX      # the phone's MAC, shown when it connects
quit
```

## 4. Stream

```bash
cd ~/ecg
source .venv/bin/activate
python3 ecg_transmitter.py --record 119 --loop
```

You want:

```
downloading mitdb/119 from PhysioNet ...
  resampled 25/36 -> ... samples @ 250 Hz
advertising ECGStream on RFCOMM channel 1 ...
```

Then in the app, clear the `tcp://...` address, type `raspberry` (or whatever
prefix matches your Pi's Bluetooth name), and press **Connect**.

Record 119 is ventricular bigeminy — every second beat is a PVC — so the
display should alternate green and red markers.

## If pybluez2 failed

The transmitter still runs, but it can't advertise the SPP service record, so
the phone won't find anything to connect to. Register it manually. `sdptool`
needs BlueZ in compatibility mode, which is off by default:

```bash
sudo nano /etc/systemd/system/dbus-org.bluez.service
#   find:  ExecStart=/usr/lib/bluetooth/bluetoothd
#   make:  ExecStart=/usr/lib/bluetooth/bluetoothd -C
sudo systemctl daemon-reload
sudo systemctl restart bluetooth
sudo sdptool add --channel=1 SP
```

Then run the transmitter again.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `No hci0 adapter found` | `sudo rfkill unblock bluetooth`, then re-run |
| pip fails building pybluez2 | `libbluetooth-dev` missing — the installer handles it, but check it actually ran |
| PhysioNet download hangs | Pi not online, or a firewall. Test with `ping physionet.org` |
| App says "No paired device matching" | Pair in phone Settings first; check the name prefix matches |
| Connects then drops instantly | Another client already holds the RFCOMM channel. Restart the transmitter |
| Transmitter runs, phone never finds it | pybluez2 missing — see above |

## Useful options

```bash
--record 119     bigeminy, best for demos
--record 106     frequent PVCs
--record 100     mostly normal, poor demo
--seconds 120    limit length (default: whole record)
--loop           repeat forever
--transport tcp --port 9000    stream over Wi-Fi instead, for testing
```

The Pi needs internet for the PhysioNet download. To work offline, download once
and pass a local path instead — or just run with `--transport tcp` from your
laptop, which is the fallback if Bluetooth misbehaves during a demo.
