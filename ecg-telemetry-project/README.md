# End-to-end ECG telemetry and beat classification

PhysioNet → Bluetooth → Android → Wi-Fi → server → back to the phone, plus the
same signal processing and classifier running on the phone itself.

```
 ecg_transmitter.py            MainActivity                    server.py
┌────────────────────┐  SPP  ┌─────────────────────┐  HTTP  ┌──────────────────┐
│ PhysioNet record   │──────▶│ FrameParser         │───────▶│ PanTompkins      │
│ resample → 250 Hz  │ RFCOMM│ EcgView (draw)      │◀───────│ BeatClassifier   │
│ frame + pace       │       │ PanTompkins  ◀── on-device ──▶│ dashboard        │
└────────────────────┘       │ BeatClassifier      │        └──────────────────┘
                             └─────────────────────┘
```

| Part | Deliverable | Where |
|---|---|---|
| 1 | PhysioNet download, 250 Hz resample, Bluetooth transmit | `transmitter/ecg_transmitter.py` |
| 2 | Capture ECG over Bluetooth on the phone | `android/.../net/BluetoothEcgClient.java`, `TcpEcgClient.java`, `FrameParser.java` |
| 3 | Display the waveform on screen | `android/.../ui/EcgView.java` |
| 4 | Ship the signal to a server over Wi-Fi | `android/.../net/CloudUploader.java`, `server/server.py` |
| 5 | Server-side processing + classification, result returned | `server/ecg_algorithms.py`, `server/server.py` |
| ★ | The whole pipeline in Java, on the phone | `android/.../dsp/PanTompkins.java`, `BeatClassifier.java` |

**iPhone instead of Android?** Possible, but iOS cannot use Bluetooth Classic
SPP at all, so the transport becomes BLE. A full SwiftUI port is in `ios/` with
a BLE transmitter in `transmitter/ble_transmitter.py`. See `ios/README.md`.

---

## Writing it up?

`report/` holds an IEEE two-column conference-format LaTeX report ready for
Overleaf, with the data figures generated from the real pipeline and marked
placeholders for the screenshots you need to take. See `report/README.md`.

## New here?

**Read `START_HERE.md`** — a complete walkthrough written for someone who has
never done any of this before, with a checkpoint at every stage and a
troubleshooting table for the things that actually go wrong.

`SETUP.md` is the older, more condensed version of the same material.

Older guide:

**Read `SETUP.md`** - a nine-phase, checkpointed walkthrough from a bare
machine to a finished system, with troubleshooting. The quick start below
assumes you already have Python and a paired Bluetooth device.

## Quick start

**1. Server** (any machine on the same network)

```bash
pip install flask
python3 server/server.py --port 8000
```

Open `http://<server-ip>:8000/` for a live strip chart with beat annotations.

**2. Transmitter**

```bash
pip install wfdb scipy numpy pybluez2
python3 transmitter/ecg_transmitter.py --record 106 --db mitdb --loop
```

Record 106 has frequent PVCs, so the classifier has something to find. Pair the
transmitting machine with the phone in Android Settings *before* connecting.

No Bluetooth adapter? `--transport tcp --port 9000` streams the identical wire
format over TCP. No internet? `--synthetic` generates a test signal locally.

**3. App**

Open `android/` in Android Studio, run on a real device (the emulator has no
Bluetooth radio). Enter the paired device's name prefix, set the server URL, and
press Connect. The **Classify on phone** switch chooses between the on-device
classifier and the server round trip.

---

## How it works

### Sampling and resampling

MIT-BIH is recorded at 360 Hz, so getting to 250 Hz is a rational resample by
25/36 — `resample_poly` low-pass filters before decimating. Worth being precise
about what that buys: content at 125–180 Hz folds down to 70–125 Hz without the
filter, so a 150 Hz EMG component reappears as a spurious 100 Hz one. It cannot
reach the 5–15 Hz QRS band — that would need energy at 235–245 Hz, which a
360 Hz recording (Nyquist 180) cannot contain — so QRS *detection* is protected
either way by its own bandpass. What the anti-alias filter actually protects is
the wideband waveform shown on screen.

### Wire protocol

```
0xA5 0x5A │ seq:u16 LE │ count:u8 │ count × sample:i16 LE (µV) │ xor:u8
```

25 samples per packet, one packet each 100 ms — about 560 B/s, trivial for SPP.
Batching matters: a packet per sample would spend more time in framing overhead
than payload.

SPP is a raw byte stream with no message boundaries. Bytes arrive in arbitrary
dribbles, the phone can attach mid-packet, and a single dropped byte would
otherwise desynchronise the link permanently. So the decoder hunts for the sync
word, sanity-checks the length byte *before trusting it*, and verifies a
checksum; any failure advances one byte and retries. Verified behaviour:

| input | result |
|---|---|
| arriving 1, 7, 56 or 4096 bytes at a time | byte-identical output every time |
| any single-byte corruption | exactly one packet lost, stream resyncs |
| connecting mid-stream | resyncs at the next packet boundary |

The length-byte check is not theoretical: without it, a corrupted count claiming
"512 samples follow" makes the decoder wait forever for bytes that never arrive,
stalling the stream permanently instead of costing one packet.

### Signal processing

Textbook Pan–Tompkins (1985), written sample-at-a-time so it streams:

```
5–15 Hz Butterworth bandpass (2 biquads)
  → 5-point derivative → square → 150 ms moving-window integration
  → adaptive SPKI/NPKI thresholds, 200 ms refractory, T-wave discrimination
```

Two things needed fixing beyond the textbook version, both found by testing:

- **Fiducial jitter.** A bandpassed QRS is biphasic, so "largest |sample|" lands
  on R for one beat and on S for the next. That jitter made the morphology
  correlation bimodal (1.00 or 0.71) and flagged ordinary beats as ectopic. Fixed
  by sliding ±45 ms and keeping the lag that best matches the running template —
  the span must exceed the R-to-S spacing or an estimate that landed on S can
  never recover.
- **QRS width.** A threshold-crossing width measurement is bimodal here: the
  search stops at a ringing null on one beat and runs into the T wave on the
  next, so widths jumped between 36 ms and 84 ms on identical beats. Replaced
  with the energy spread about R, `2·√(Σf²(k−r)² / Σf²)`, which is smooth,
  threshold-free, and dominated by the QRS.

### Classification

Features are **ratios against the patient's own running normal beat**, not
absolute millivolts and milliseconds — so one set of thresholds survives changes
of lead, electrode placement and patient:

`rr_prev`, `rr_next`, `rr_ratio`, `rr_next_ratio`, `width_ratio`, `amp_ratio`,
`corr` (correlation with the running normal template), `rr_asym`, `width`.

Each beat is held back one cycle before classification so the *following* RR
interval is known. That matters: the classic discriminator between a PVC and an
atrial premature beat is the pause after it. A PVC does not reset the sinus
node, so the next sinus beat lands on schedule and the pause is fully
compensatory; an atrial ectopic resets it and the pause is shorter.

Two classifiers, same feature vector:

- **`RuleClassifier`** — works with no training data. Ventricular requires the
  complex to be *both* morphologically unlike the normal *and* broader than it;
  demanding both keeps ordinary beats adjacent to an ectopic, whose correlation
  window is contaminated by the neighbour, out of the class.
- **`LinearClassifier`** — multinomial logistic regression fitted on MIT-BIH.
  Linear specifically so it ports to the phone as a JSON file of weights; a
  random forest would not.

```bash
pip install wfdb scikit-learn numpy
python3 server/train_classifier.py
cp server/model.json android/app/src/main/assets/
```

Training uses the de Chazal DS1/DS2 **patient-disjoint** split. Splitting beats
at random instead would let the model memorise one patient's QRS shape and then
grade itself on that same patient — a leak that inflates published
beat-classification accuracy dramatically.

---

## Phone and server compute the same thing

`PanTompkins.java` is a line-for-line port of `ecg_algorithms.py`, down to the
filter coefficients. That claim is checked, not asserted:

```bash
$ python3 tools/cross_check.py
python: 71 beats   java: 71 beats
MATCH - every beat index, feature and label is identical.
```

It compiles the Android DSP classes with plain `javac` (they touch nothing but
`java.lang` and `java.util`), runs both over the same signal, and diffs every
beat index, feature and label to six decimal places. **Run it after touching
either implementation** — a silent divergence between the two is the most likely
bug in this project.

## Measured behaviour

- QRS detection: **97.3%** sensitivity on synthetic test signals.
- Rule classifier: **98.6%** over 432 synthetic beats, all ectopics caught.
- Server: **1.8 ms** to process a 2 s batch — roughly 1000× real time, so one
  server handles many phones.
- On-device cost: ~40 flops per sample; at 250 Hz this is negligible.

**These synthetic numbers are a sanity check, not validation.** The generator
produces cleaner, more regular beats than a real patient. For numbers worth
quoting, run `train_classifier.py`, which reports per-class recall, precision,
F1 and a confusion matrix on held-out patients from MIT-BIH.

## Design decisions worth knowing

- **Detector state persists per device across HTTP requests.** The phone posts
  2 s batches; analysing each independently would miss every QRS on a batch
  boundary and reset the adaptive thresholds and RR history 30 times a minute.
- **The upload queue is bounded and drops the oldest batch** when the network
  falls behind. For a live monitor, stale ECG is worthless — losing two seconds
  beats falling permanently behind, and an unbounded queue grows until the
  process is killed.
- **Bluetooth reads, DSP and drawing all happen off the main thread**; only the
  text updates are posted to the UI looper. Redraws are throttled to ~30 fps
  rather than firing on each 100 ms block.
- **Sweep-style display** rather than shifting the array each frame, which would
  cost an O(n) memmove per frame and make the trace shimmer.

## Limitations

- No lead-off or saturation detection; a disconnected electrode produces noise
  that the detector will happily find "beats" in.
- The `Other` class lumps fusion, paced and unclassifiable beats together, and
  is the weakest of the four.
- Two-second batching means the cloud path reports a beat up to ~2 s after it
  happened. The on-device path has roughly one beat of latency.
- `usesCleartextTraffic` is needed for plain HTTP to a lab server; use HTTPS for
  anything real.
- **Not a medical device.** This is coursework — it is not validated for
  clinical use and must not be used to make decisions about anyone's health.

## Layout

```
SETUP.md                           step-by-step build guide - start here
report/report.tex                  IEEE two-column report for Overleaf
tools/gen_figures.py               regenerates the report's data figures
transmitter/ecg_transmitter.py     PhysioNet → 250 Hz → Bluetooth/TCP
server/ecg_algorithms.py           detector + features + classifiers
server/server.py                   Flask API + live dashboard
server/train_classifier.py         MIT-BIH training and evaluation
tools/cross_check.py               proves the Java matches the Python
tools/simulate_phone.py            test the server with no phone
tools/gen_reference_vectors.py     freezes Python output for the Swift tests
transmitter/ble_transmitter.py     BLE peripheral transmitter (for iOS)
ios/                               SwiftUI app (BLE instead of SPP)
android/                           Android Studio project
```
