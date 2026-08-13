# Step-by-step: building the whole system from scratch

Nine phases. Each ends with a **checkpoint** — something you can see that proves
that phase works. Do not move on until the checkpoint passes; every phase
depends on the one before, and debugging Bluetooth pairing while the server is
also broken is how people lose a weekend.

The order is deliberate: **everything that can be tested without hardware is
tested first.** By the end of Phase 3 you'll have Parts 1, 4 and 5 working with
no phone and no Bluetooth radio involved.

| Phase | What you get | Assignment part | Time |
|---|---|---|---|
| 1 | Code running, DSP verified | — | 30 min |
| 2 | Real ECG from PhysioNet at 250 Hz | 1 | 20 min |
| 3 | Server classifying, dashboard live | 4, 5 | 30 min |
| 4 | App built and installed | — | 45–90 min |
| 5 | Live ECG on the phone screen | 1, 2, 3 | 30–60 min |
| 6 | Phone → cloud → phone round trip | 4, 5 | 20 min |
| 7 | Classification on the phone itself | ★ challenge | 10 min |
| 8 | Classifier trained on MIT-BIH | — | 30 min |
| 9 | Numbers and screenshots for the report | — | 30 min |

---

## Before you start

**You need:**
- A computer (Windows, macOS or Linux) with Python 3.9+
- An Android phone — **a real one**; the emulator has no Bluetooth radio
- A USB cable for the phone
- Both devices on the same Wi-Fi network

**Optional but ideal:** a Raspberry Pi. It makes Phase 5 much easier and matches
the "IoT device" wording in the assignment.

### Using an iPhone instead?

Phases 1--3 and 8--9 are identical; the phone-side phases change. iOS has **no
access to Bluetooth Classic SPP**, so the transport must be BLE and the
transmitter must be `ble_transmitter.py`. You will also need a Mac with Xcode.
Read `ios/README.md`, then follow Phases 1--3 here as written and substitute the
iOS instructions for Phases 4--7.

### Pick your transmitter path now

Part 1 says transmit over Bluetooth. The catch is that acting as a Bluetooth
*server* from Python is easy on Linux and genuinely painful elsewhere.

| Your machine | Path | Notes |
|---|---|---|
| Linux laptop or Raspberry Pi | **Bluetooth** | Works properly. Use this if you can. |
| Windows or macOS | **TCP fallback** | Identical packets over a TCP socket. |
| Windows/macOS + a Pi | Bluetooth from the Pi | Best of both. |

The app speaks both. The wire format, the parser, the DSP and everything
downstream are identical — only the socket differs. If you must demo over TCP,
say so in your report and explain that the transport is abstracted behind
`EcgSource`; that's an engineering decision, not a shortcut.

---

## Phase 1 — Base setup and verifying the DSP

No hardware. This proves the signal processing works before anything can
confuse the picture.

**1.1 Install Python 3.9+**

```bash
python3 --version     # need 3.9 or newer
```
Windows: install from python.org and tick **Add Python to PATH**.

**1.2 Unpack the project and create a virtual environment**

```bash
unzip ecg-telemetry-project.zip -d ecg-project
cd ecg-project

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
```

Your prompt should now start with `(.venv)`. It must say that in **every**
terminal you open from here on — that trips everyone up at least once.

**1.3 Install dependencies**

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

If `pybluez2` fails on Windows or macOS, ignore it — you're on the TCP path and
don't need it. On Debian/Ubuntu/Raspberry Pi OS, install its build dependencies
first:

```bash
sudo apt update
sudo apt install -y python3-dev libbluetooth-dev bluez
```

**1.4 Install a JDK** (only for the verification tool in 1.5)

```bash
sudo apt install -y default-jdk      # Debian/Ubuntu/Pi
# macOS: brew install openjdk        Windows: skip; Android Studio brings one
javac -version
```

**1.5 CHECKPOINT — verify the Java matches the Python**

```bash
python3 tools/cross_check.py
```

Expected:
```
python: 71 beats   java: 71 beats
MATCH - every beat index, feature and label is identical.
```

This compiles the phone's DSP with plain `javac`, runs it and the server's
Python over the same signal, and diffs every beat, feature and label. If it says
MATCH, the hardest part of the project is already correct. Re-run it any time
you edit either implementation.

---

## Phase 2 — Real ECG from PhysioNet (Part 1)

**2.1 Confirm you can reach PhysioNet**

```bash
python3 -c "
import wfdb
sig, f = wfdb.rdsamp('106', pn_dir='mitdb')
print('samples:', sig.shape, 'fs:', f['fs'], 'leads:', f['sig_name'])"
```

Expect roughly `(650000, 2) fs: 360`. The first download takes a moment.

Use **record 106** (frequent PVCs). Record 100 is nearly all normal beats and
gives the classifier nothing to find — a common reason people conclude their
classifier "doesn't work".

**2.2 Understand the resampling**

MIT-BIH is 360 Hz; you need 250 Hz — a rational resample by 25/36, done with
`resample_poly`, which low-pass filters *before* decimating. Be precise about
why in your report: without the filter, 125–180 Hz content folds down to
70–125 Hz (a 150 Hz EMG artefact reappears at 100 Hz). It cannot reach the
5–15 Hz QRS band, since that would need energy at 235–245 Hz which a 360 Hz
recording cannot contain. So detection is protected by its own bandpass anyway;
the anti-alias filter protects the waveform you display.

**2.3 CHECKPOINT — run the transmitter**

```bash
python3 transmitter/ecg_transmitter.py --record 106 --db mitdb \
        --transport tcp --port 9000 --loop
```

Expected:
```
downloading mitdb/106 from PhysioNet ...
  650000 samples @ 360 Hz, lead MLII
  resampled 25/36 -> 451389 samples @ 250 Hz
waiting for a phone on tcp://192.168.1.42:9000 ...
```

**Write down that IP address** — the phone needs it. Leave this running, or
Ctrl-C it for now.

---

## Phase 3 — Server and cloud classification (Parts 4 and 5)

Still no phone.

**3.1 Start the server** (new terminal, activate the venv again)

```bash
python3 server/server.py --port 8000
```

**3.2 Find your machine's LAN IP**

```bash
ip addr show | grep "inet "        # Linux / Pi
ifconfig | grep "inet "            # macOS
ipconfig                           # Windows -> IPv4 Address
```

Take the `192.168.x.x` or `10.x.x.x` one. **Not** `127.0.0.1` — that means "this
machine only" and the phone can never reach it.

**3.3 Open a firewall hole if needed**

```bash
sudo ufw allow 8000/tcp            # Linux, if ufw is active
```
Windows: allow Python through the Windows Defender Firewall prompt when it
appears, for **Private** networks.

**3.4 CHECKPOINT — drive the server with the phone simulator**

Third terminal:

```bash
python3 tools/simulate_phone.py --url http://localhost:8000/api/ecg \
        --record 106 --seconds 60
```

Expected:
```
posting 60.0 s of ECG to http://localhost:8000/api/ecg
  batch  30  hr  72 bpm  server   2.1 ms  beats NVN
totals: {'Normal': 58, 'Ventricular': 11, ...}
```

This does exactly what the app will do — 2-second batches over HTTP — so Parts 4
and 5 are now proven end to end. Open **http://localhost:8000/** in a browser
and watch the trace with coloured beat markers.

Then repeat from another device on your Wi-Fi using the LAN IP instead of
`localhost`. If that fails, it's a firewall or a "guest network client
isolation" problem — solve it now, not in Phase 6.

---

## Phase 4 — Building the Android app (Parts 2 and 3)

**4.1 Install Android Studio** from developer.android.com. Accept the default
SDK components. Budget 30–60 minutes and several GB.

**4.2 Open the project**

`File → Open` → select the **`android`** folder inside the project (the one
containing `settings.gradle`), not the project root.

Gradle sync runs automatically and needs internet. If it complains about a
missing SDK, click the link in the error — Studio installs it for you.

**4.3 Enable developer mode on the phone**

1. Settings → About phone
2. Tap **Build number** seven times
3. Back → System → Developer options → enable **USB debugging**
4. Plug in via USB and accept the "Allow USB debugging?" prompt

Your phone should now appear in Studio's device dropdown.

**4.4 Set the server URL**

Open `app/src/main/res/layout/activity_main.xml` and change the hardcoded
`http://192.168.1.100:8000/api/ecg` to your Phase 3 IP. You can also edit it in
the app at runtime.

**4.5 Allow plain HTTP to your server**

Android blocks cleartext HTTP by default since Android 9, and this is the single
most common way the upload silently fails. `res/xml/network_security_config.xml`
already permits it for common private ranges. **If your server IP isn't
192.168.x or 10.x, add it there.**

**4.6 CHECKPOINT — install and run**

Press ▶. The app should launch showing an empty grid, "not connected", and
`-- bpm`. Nothing streams yet — that's Phase 5.

---

## Phase 5 — Live ECG on the phone (Parts 1, 2 and 3)

### Path A — Bluetooth (Linux or Raspberry Pi)

**5A.1 Make the machine pairable**

```bash
bluetoothctl
```
Then, inside it:
```
power on
agent on
default-agent
discoverable on
pairable on
```
Leave it open.

**5A.2 Pair from the phone**

Settings → Bluetooth → find the machine → pair. Confirm the matching passkey on
**both** screens. In `bluetoothctl` you'll see the connection; then:
```
trust <PHONE_MAC>
quit
```

Pairing must be done in Android Settings, not in the app. The app deliberately
only looks at already-paired devices — in-app discovery needs a scan dialog and
location permission and adds nothing here.

**5A.3 Start the transmitter over Bluetooth**

```bash
python3 transmitter/ecg_transmitter.py --record 106 --db mitdb --loop
```

Expected: `advertising ECGStream on RFCOMM channel 1 ...`

If instead it warns that pybluez2 is missing and the phone then can't find the
service, register the SPP record manually. `sdptool` needs bluetoothd in compat
mode:

```bash
sudo nano /etc/systemd/system/dbus-org.bluez.service
#   change:  ExecStart=/usr/lib/bluetooth/bluetoothd
#   to:      ExecStart=/usr/lib/bluetooth/bluetoothd -C
sudo systemctl daemon-reload
sudo systemctl restart bluetooth
sudo sdptool add --channel=1 SP
```

**5A.4 Connect from the app**

Type the paired machine's name (or a prefix, e.g. `raspberry`) into the device
field, leave **Classify on phone** ON, press **Connect**.

### Path B — TCP (Windows, macOS, or the emulator)

**5B.1** Start the transmitter:

```bash
python3 transmitter/ecg_transmitter.py --record 106 --db mitdb \
        --transport tcp --port 9000 --loop
```

**5B.2** In the app's device field type `tcp://192.168.1.42:9000` using your own
IP, then **Connect**. No Bluetooth permission is requested for a `tcp://` target.

On the emulator, the host machine is always `10.0.2.2`, so use
`tcp://10.0.2.2:9000`.

**5.5 CHECKPOINT**

Within about two seconds you should see a green ECG trace sweeping across the
grid, a plausible heart rate (60–100 bpm for record 106), and coloured tick
marks above each beat — green normal, red ventricular, amber supraventricular.

**Parts 1, 2 and 3 are now complete.**

---

## Phase 6 — Phone to cloud and back (Parts 4 and 5)

**6.1** Make sure `server.py` is still running and the URL field holds your LAN
IP.

**6.2** Turn **Classify on phone** OFF and press Connect.

**6.3 CHECKPOINT**

- The result line now reads e.g. `Ventricular  server 43 ms`
- The server terminal logs a `POST /api/ecg` roughly every 2 seconds
- The dashboard at `http://<ip>:8000/` shows your phone under the device
  dropdown, with its live trace

The ~2 s lag is intentional: 500-sample batches. One HTTP request per 25-sample
Bluetooth block would mean 10 requests a second, each with more protocol
overhead than payload, and would drain the battery.

**Parts 4 and 5 are now complete.**

---

## Phase 7 — Classification on the phone (the challenge)

Already built. Turn **Classify on phone** back ON.

Now the difference matters: with the switch ON, `PanTompkins.java` and
`BeatClassifier.java` run on the phone and nothing is uploaded — pull the Wi-Fi
and it keeps classifying. With it OFF, the phone is a dumb relay.

**CHECKPOINT** — run the same record through both modes and compare the beat
counts. They should agree, because `tools/cross_check.py` guarantees the two
implementations compute identical results. That comparison is the single best
figure to put in your report.

---

## Phase 8 — Train the classifier on real annotated data

The rule-based classifier works with no training data, but real numbers need
real annotations.

**8.1 Train**

```bash
python3 server/train_classifier.py
```

This downloads ~44 MIT-BIH records, so give it 10–30 minutes. It runs the same
detector used at inference, pairs each detected beat with the nearest
cardiologist annotation, fits the model, and prints per-class recall, precision,
F1 and a confusion matrix on **held-out patients**.

**8.2 Deploy the same weights to both sides**

```bash
cp server/model.json android/app/src/main/assets/
```

Restart the server (it loads `model.json` at startup and will now log
`loaded trained model`), and rebuild the app.

**8.3 CHECKPOINT** — `curl http://localhost:8000/healthz` reports
`"classifier": "LinearClassifier"` instead of `RuleClassifier`.

**Why logistic regression and not a random forest?** It has to run on the phone
from a JSON file of weights. A linear model ports to Java in twenty lines; a
forest doesn't. With ratio features the problem is close to linearly separable,
so the accuracy cost is small and the deployment win is large.

**Why the DS1/DS2 split?** Training and test records contain *different
patients*. Splitting beats at random instead lets the model memorise one
patient's QRS shape and then grade itself on that same patient — a leak that
inflates published beat-classification accuracy dramatically. If you report a
number, report this one.

---

## Phase 9 — Evidence for your report

Collect:

1. **Screenshot** of the app mid-stream with a red ventricular marker visible.
2. **Screenshot** of the server dashboard with the beat table.
3. **Terminal output** of `tools/cross_check.py` showing MATCH — this is your
   evidence that the on-device and server implementations are equivalent, which
   is otherwise just an assertion.
4. **The confusion matrix** from `train_classifier.py` on held-out patients.
5. **Latency**: server `processing_ms` (~2 ms per 2 s batch, roughly 1000× real
   time) and the round-trip time the app displays.
6. **Robustness**: mention that the frame parser gives byte-identical output
   whether data arrives 1 or 4096 bytes at a time, and loses exactly one packet
   per corrupted byte.

Three things worth writing up as engineering judgement rather than just
"it works":

- **Detector state persists per device across HTTP requests.** Analysing each
  2 s batch independently would miss every QRS on a batch boundary and reset the
  adaptive thresholds ~30 times a minute.
- **The upload queue is bounded and drops the oldest batch** when the network
  falls behind. For a live monitor, stale ECG is worthless; an unbounded queue
  just grows until the process is killed.
- **Each beat is classified one cycle late, on purpose**, so the *following* RR
  interval is known. The classic discriminator between a PVC and an atrial
  premature beat is the pause after it: a PVC doesn't reset the sinus node, so
  the pause is fully compensatory.

And state the limitation plainly: this is coursework, validated on a database,
not a medical device.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ModuleNotFoundError` | venv not activated in that terminal. `source .venv/bin/activate` |
| PhysioNet download hangs | Institutional firewall. Try another network, or use `--synthetic`. |
| `pybluez2` won't install | Only needed for Bluetooth on Linux. Install `libbluetooth-dev`, or use the TCP path. |
| App can't find the paired device | Pair in Android **Settings** first. Check the name prefix matches, and that Bluetooth permission was granted. |
| Connects, then immediately drops | Something else is already using the RFCOMM channel — one client at a time. Restart the transmitter. |
| Flat line, no beats | The 2 s learning phase must pass first. If it persists, the lead may be near-silent — try `--channel 1`. |
| Beats detected but all "Normal" | You're probably on record 100. Use 106, 119 or 208. |
| `Cleartext HTTP not permitted` | Add your server IP to `res/xml/network_security_config.xml`. |
| Uploads time out | Wrong IP (not `127.0.0.1`), firewall, or the phone is on mobile data instead of Wi-Fi. |
| Dashboard empty | Pick your device in the dropdown; it only appears after the first POST. |
| Gradle sync fails | Needs internet. `File → Invalidate Caches / Restart`. |
| `cross_check.py` says MISMATCH | You edited one implementation and not the other. Both must change together. |

---

## Fastest possible demo

If you're short on time and just need something to show:

```bash
# terminal 1
python3 server/server.py --port 8000
# terminal 2
python3 tools/simulate_phone.py --url http://localhost:8000/api/ecg --record 106
# browser
open http://localhost:8000/
# terminal 3
python3 tools/cross_check.py
```

That demonstrates Parts 1, 4 and 5 plus the equivalence proof in about five
minutes, with no phone at all. Then build the app for Parts 2 and 3.
