# iOS build

## The one thing that changes: Bluetooth

**iOS cannot use Bluetooth Classic SPP/RFCOMM.** Apple restricts that profile to
MFi-certified hardware through the External Accessory framework; a normal
third-party app has no API for it at any price. No amount of Swift will make the
Android transport work on an iPhone.

So the iOS build uses **BLE (Core Bluetooth)** instead. The transmitter becomes a
BLE peripheral advertising a GATT service, and the phone subscribes to
notifications on it.

Everything above the transport is unchanged:

| Layer | Android | iOS |
|---|---|---|
| Transport | RFCOMM / SPP socket | BLE GATT notifications |
| Packet format | identical | identical |
| Frame parser | `FrameParser.java` | `FrameParser.swift` |
| DSP | `PanTompkins.java` | `PanTompkins.swift` |
| Classifier | `BeatClassifier.java` | `BeatClassifier.swift` |
| Server API | identical | identical |

The parser was already a byte-stream parser tolerant of arbitrary fragmentation,
which is exactly what BLE notification chunking produces — so a packet split
across several notifications reassembles with no changes at all.

Bandwidth is a non-issue: 250 Hz × 2 bytes = 500 B/s, against BLE's several kB/s.

## What you need

- **A Mac with Xcode.** There is no way around this; iOS apps cannot be built on
  Windows or Linux.
- **A real iPhone.** The simulator has no Bluetooth radio.
- A free Apple ID works for on-device testing, but the app expires after 7 days
  and must be reinstalled. A paid developer account (US$99/yr) removes that.
- A **Linux machine or Raspberry Pi** for the transmitter. BLE peripheral mode
  needs BlueZ; macOS and Windows cannot easily act as a BLE peripheral either.

## Setting up the Xcode project

1. Xcode → **File → New → Project → iOS → App**
   - Interface **SwiftUI**, Language **Swift**, product name **ECGMonitor**
2. Delete the generated `ContentView.swift` and `ECGMonitorApp.swift`.
3. Drag in everything from `ios/ECGMonitor/` (tick **Copy items if needed**).
4. Add the required Info.plist keys — see `Info-additions.plist`.
   **`NSBluetoothAlwaysUsageDescription` is mandatory:** without it iOS does not
   merely deny permission, it terminates the app the moment it creates a
   `CBCentralManager`.
5. For the tests: **File → New → Target → Unit Testing Bundle**, add
   `ios/ECGMonitorTests/EquivalenceTests.swift`, and add
   `Resources/reference_vectors.json` to the **test target's** Copy Bundle
   Resources phase.
6. Select your iPhone, set a Signing Team, and run.

## Running the transmitter

On the Pi or Linux box:

```bash
pip install bless wfdb scipy numpy
sudo python3 transmitter/ble_transmitter.py --record 106 --loop
```

Root is needed because BLE advertising requires privileges. If advertising
fails, enable BlueZ experimental mode:

```bash
sudo sed -i 's|^ExecStart=.*bluetoothd.*|& --experimental|' \
    /lib/systemd/system/bluetooth.service
sudo systemctl daemon-reload && sudo systemctl restart bluetooth
```

**Check it before writing any Swift.** Install **nRF Connect** or **LightBlue**
on the phone, scan, and you should see `ECG-Sim` advertising the Nordic UART
service with notifications streaming. If that works, the transmitter is fine and
any remaining problem is in the app.

## Verifying the port

The Android build proves the Java matches the Python by compiling it with
`javac` and diffing (`tools/cross_check.py`). Swift cannot be compiled outside
Xcode, so the check is inverted: the Python output is frozen into
`reference_vectors.json`, and the Xcode test asserts the Swift reproduces it.

```bash
python3 tools/gen_reference_vectors.py    # after any DSP change
```
then **⌘U** in Xcode. `testMatchesPythonReference` compares every beat index,
feature and label to six decimals. It is the iOS equivalent of the `MATCH` line,
and it is what you should screenshot for Fig. 7 of the report.

There are two more tests: the frame parser is checked against fragmentation at
MTUs from 1 to 512 bytes and against a corrupted length byte, and throughput is
asserted to exceed 10× real time.

**I was not able to compile this Swift myself** — no Swift toolchain was
available in the environment where it was written, unlike the Java, which was
compiled and diffed directly. Expect to fix a small syntax error or two on first
build. The algorithms are transcribed line-for-line from the verified Python, so
any problems should be Swift-level, not logic-level — and the equivalence test
will tell you definitively.

## iOS-specific limitations worth noting in the report

- **Background execution.** iOS suspends apps aggressively. Continuous
  monitoring with the screen off needs the `bluetooth-central` background mode,
  and even then iOS may throttle. The app currently keeps the screen awake
  instead, which is honest for a demo but not a shippable strategy.
- **No Bluetooth Classic** at all, as above — worth stating explicitly, since it
  is a genuine platform constraint rather than a design choice.
- **Connection interval.** iOS picks it, typically 15–30 ms. At 10 packets/s this
  is ample, but it caps how far the packet rate could be pushed.
