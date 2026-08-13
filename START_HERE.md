# START HERE

A complete ECG monitoring system: a Raspberry Pi streams a real ECG recording
over Bluetooth to an Android phone, the phone displays it and either analyses it
on the spot or sends it to a server, and each heartbeat is labelled normal or
abnormal.

**You do not need to understand ECG or signal processing to get this running.**
Follow the steps in order. Each one ends with something you can see, so you
always know whether it worked.

---

## What you need

| Thing | Notes |
|---|---|
| A computer | Windows, macOS or Linux. Runs the server and builds the app. |
| An Android phone | A **real** one. The emulator has no Bluetooth. Android 7+. |
| A Raspberry Pi | Model 3, 4, 5 or Zero W (these have built-in Bluetooth). |
| Wi-Fi | All three devices on the **same** network. |
| A USB-C power supply for the Pi | 5V 3A. A weak supply causes random failures. |

**Campus Wi-Fi often will not work.** Many university networks stop devices from
talking to each other. If things mysteriously cannot connect, put all three
devices on a phone hotspot instead.

**No Raspberry Pi?** You can still do everything except the literal Bluetooth
step — the system also streams over Wi-Fi, and the setup is identical. See
Step 6b.

---

## How long it takes

| Step | What you get | Time |
|---|---|---|
| 1 | Software installed and checked | 20 min |
| 2 | Server running, ECG classified | 20 min |
| 3 | Proof the phone and server agree | 10 min |
| 4 | The app built and installed | 60–90 min |
| 5 | Live ECG on the phone screen | 20 min |
| 6 | Bluetooth from the Raspberry Pi | 45 min |
| 7 | Classifier trained on real data | 30 min |

Steps 1–3 need no phone and no Pi. Do them first — if something is wrong, you
want to find out before adding hardware to the picture.

---

# STEP 1 — Install the software

### 1.1 Install Python

Download from [python.org/downloads](https://www.python.org/downloads/). Get
version 3.11 or 3.12.

**On Windows, tick "Add python.exe to PATH"** on the first screen of the
installer. Almost everyone misses this box, and everything fails afterwards with
"Python was not found".

Close any open terminal, open a new one, and check:

```
python --version
```

If Windows opens the Microsoft Store instead, go to
**Settings → Apps → Advanced app settings → App execution aliases** and switch
off `python.exe` and `python3.exe`.

*(On macOS and Linux, use `python3` everywhere this guide says `python`.)*

### 1.2 Unpack the project

Extract `ecg-telemetry-project.zip`. On Windows, right-click → Extract All.
Choose a simple path such as `C:\ecg-project`.

Open a terminal **in that folder**:

```
cd C:\ecg-project
```

Type `dir` (Windows) or `ls`. You should see `server`, `tools`, `transmitter`,
`android` and `requirements.txt`. If you only see one folder, go into it — some
zip tools add an extra layer.

### 1.3 Create a virtual environment

This keeps the project's packages separate from the rest of your system.

```
python -m venv .venv
.venv\Scripts\activate
```

On macOS/Linux the second line is `source .venv/bin/activate`.

**If Windows says "running scripts is disabled":**

```
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Answer `Y`, then try activating again.

Your prompt should now start with `(.venv)`. **It must say that in every
terminal you open from now on.** When something later says a package is not
installed, this is nearly always why.

### 1.4 Install the packages

```
pip install -r requirements.txt
```

If `pybluez2` fails to build, ignore it — it is only needed on the Raspberry Pi.
If the failure stops the whole install, run this instead:

```
pip install wfdb scipy numpy flask scikit-learn matplotlib pandas
```

### 1.5 Check everything

```
python verify_setup.py
```

This checks each requirement and tells you how to fix anything missing. **It
also prints your computer's network address** — something like `192.168.1.26`.
Write it down; you will need it twice later.

---

# STEP 2 — Run the server

### 2.1 Start it

```
python server\server.py --port 8000
```

Leave this terminal running and do not close it.

Windows may ask whether to allow Python through the firewall. **Click Allow**,
and make sure "Private networks" is ticked. You need this for the phone later.

### 2.2 Feed it some ECG

Open a **second** terminal and set it up the same way:

```
cd C:\ecg-project
.venv\Scripts\activate
python tools\simulate_phone.py --url http://localhost:8000/api/ecg --record 119
```

This pretends to be the phone: it downloads a real ECG recording, cuts it into
2-second pieces, and sends them to the server exactly as the app will.

You should see lines like:

```
batch  12  hr  72 bpm  server   2.1 ms  beats NVN
```

### 2.3 Look at it

Open **http://localhost:8000/** in a browser while it is running. Pick
`simulated-phone` from the dropdown.

You should see a moving ECG trace with coloured marks above the beats. Recording
119 alternates normal and abnormal beats, so the marks alternate green and red.

**This is objectives 4 and 5 finished.** Take a screenshot.

> **Which recording?** Use **119** or **106**, which contain plenty of abnormal
> beats. Recording **100** is almost entirely normal, and using it is the usual
> reason people think the classifier is broken.

---

# STEP 3 — Prove the phone and server agree

The heartbeat analysis is written twice: in Python for the server and in Java
for the phone. If those two ever disagreed, the system would give different
answers depending on where it ran. This check proves they do not.

You need a Java compiler. If you have not installed Android Studio yet:

```
winget install Microsoft.OpenJDK.21
```

Then **close the terminal and open a new one** (PATH changes do not reach open
windows), and:

```
cd C:\ecg-project
.venv\Scripts\activate
python tools\cross_check.py
```

You want:

```
classifier: TreeClassifier
python: 71 beats   java: 71 beats
MATCH - every beat index, feature and label is identical.
```

**Screenshot this.** It compiles the phone's code, runs both versions over the
same signal, and compares every beat and every measurement. It is the strongest
single piece of evidence in the project.

Run it again any time you change the analysis code.

---

# STEP 4 — Build the Android app

### 4.1 Install Android Studio

From [developer.android.com/studio](https://developer.android.com/studio).
Accept the default components. This is a large download.

### 4.2 Create the project

Do **not** open the `android` folder directly. Gradle version mismatches make
that fail. Instead let Studio create a project and copy the code in.

`File → New → New Project → Empty Views Activity` (Views, **not** Compose), then:

| Field | Value |
|---|---|
| Name | `ECGMonitor` |
| Package name | `com.example.ecg` |
| Language | **Java** |
| Minimum SDK | **API 24** |

The package name must be exactly `com.example.ecg` — the code files declare it.

Let it finish syncing.

### 4.3 Copy the code in

Studio put the project somewhere like
`C:\Users\YourName\AndroidStudioProjects\ECGMonitor`. To confirm, right-click
the project name → **Open In → Explorer**.

In PowerShell, set the first two lines to your paths and run the rest:

```powershell
$dest = "C:\Users\YourName\AndroidStudioProjects\ECGMonitor\app\src\main"
$src  = "C:\ecg-project\android\app\src\main"

xcopy /E /I /Y "$src\java\com\example\ecg" "$dest\java\com\example\ecg"
xcopy /Y "$src\res\layout\activity_main.xml" "$dest\res\layout\"
xcopy /I /Y "$src\res\xml" "$dest\res\xml"
```

**Delete Studio's leftover starter file**, or it will not compile:

```powershell
Remove-Item -Recurse -Force "$dest\java\com\example\ecgmonitor" -ErrorAction SilentlyContinue
```

Check you have 9 Java files:

```powershell
Get-ChildItem "$dest\java" -Recurse -Filter *.java | Measure-Object
```

### 4.4 Edit the manifest

Open `app/src/main/AndroidManifest.xml`. If it looks like one long line with no
line breaks, you are looking at the read-only **Merged Manifest** preview — click
the **AndroidManifest.xml** tab at the bottom of the editor.

Add these lines just after `<manifest ...>` and before `<application`:

```xml
<uses-permission android:name="android.permission.BLUETOOTH_CONNECT" />
<uses-permission android:name="android.permission.BLUETOOTH_SCAN"
    android:usesPermissionFlags="neverForLocation" />
<uses-permission android:name="android.permission.BLUETOOTH"
    android:maxSdkVersion="30" />
<uses-permission android:name="android.permission.BLUETOOTH_ADMIN"
    android:maxSdkVersion="30" />
<uses-permission android:name="android.permission.INTERNET" />
```

Then add one line **inside** the `<application ...>` tag, among the other
`android:` lines:

```xml
android:networkSecurityConfig="@xml/network_security_config"
```

That last one matters more than it looks. Android blocks plain HTTP by default,
and without it every upload fails with a message that looks like a bug in the
app but is actually a platform rule.

### 4.5 Set the server address

Open `app/src/main/res/layout/activity_main.xml`, find
`http://192.168.1.100:8000/api/ecg`, and change it to your own address from
Step 1.5.

### 4.6 Turn on developer mode on the phone

1. Settings → **About phone** → **Software information**
2. Tap **Build number** seven times
3. Back out → Settings → **Developer options** → turn on **USB debugging**

**On Samsung phones, USB debugging may be greyed out with "blocked by Auto
Blocker".** Go to Settings → Security and privacy → **Auto Blocker** and turn it
off.

### 4.7 Connect the phone

Plug it in with a USB cable and accept the "Allow USB debugging?" prompt.
Check the computer can see it:

```powershell
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" devices
```

You want your phone listed with `device` beside it.

**If the list is empty, try a different USB cable first.** Many cables only carry
power, so the phone charges but stays invisible. This wastes more people's time
than anything else in this guide.

**Or skip USB entirely** — wireless works well and the phone needs to be on your
Wi-Fi anyway:

Developer options → **Wireless debugging** → on → **Pair device with pairing
code**. Then:

```powershell
cd "$env:LOCALAPPDATA\Android\Sdk\platform-tools"
.\adb.exe pair 192.168.1.147:41234
```

Use the address and 6-digit code shown on the phone. Then `.\adb.exe devices`.

*(Wireless debugging drops when the phone sleeps. Just pair again — the port
number changes each time.)*

### 4.8 Install it

Press the green **▶** button.

The app should open showing an empty grid, `-- bpm`, and "not connected". Nothing
is streaming yet — that comes next.

---

# STEP 5 — See the ECG on the phone

Test over Wi-Fi first. It has fewer things to go wrong than Bluetooth, so if
something breaks you know where to look.

**Terminal 1** (leave the server running from Step 2, or restart it):

```
python server\server.py --port 8000
```

**Terminal 2:**

```
python transmitter\ecg_transmitter.py --record 119 --transport tcp --port 9000 --loop
```

Wait for `waiting for a phone on tcp://192.168.1.26:9000`.

**On the phone**, in the top text box type your own address:

```
tcp://192.168.1.26:9000
```

Leave **Classify on phone** switched ON and press **Connect**.

Within about two seconds you should see a green ECG trace sweeping across the
screen with coloured marks above the beats.

**This is objectives 2 and 3 finished.** Screenshot it.

### Then test the server path

Press **Disconnect**, switch **Classify on phone** OFF, press **Connect** again.

Now the phone is sending the signal to your computer and getting the answers
back. The text should read something like `Ventricular  server 43 ms`, and your
phone should appear in the dropdown on the dashboard.

That switch is the difference between two parts of the project: **on** means the
phone does everything itself, **off** means the server does the analysis.

**If it hangs on "connecting":** Windows Firewall. In an admin PowerShell:

```powershell
netsh advfirewall firewall add rule name="ECG TCP" dir=in action=allow protocol=TCP localport=9000
```

---

# STEP 6 — Bluetooth from the Raspberry Pi

### 6.1 Set up the Pi

Easiest without a monitor. Put the SD card in your computer, open **Raspberry Pi
Imager**, and **before writing** press `Ctrl+Shift+X` to set:

- Enable SSH
- A username and password
- Your Wi-Fi name, password and country

Write the card, put it in the Pi, power it on, wait a minute, then from your
computer:

```
ssh pi@raspberrypi.local
```

*(The Pi 4 uses **micro**-HDMI, not full-size, which is why going without a
monitor is usually easier. Its USB-C port is power only — it cannot be used to
connect to your computer.)*

### 6.2 Install

Copy `pi-bundle.zip` across:

```powershell
scp pi-bundle.zip pi@raspberrypi.local:~/
```

Then on the Pi:

```bash
unzip pi-bundle.zip -d ecg
cd ecg
chmod +x setup_pi.sh
./setup_pi.sh
```

This takes several minutes; some packages are compiled from source on the Pi.

### 6.3 Pair with the phone

Pairing is done in the phone's **Settings**, not in the app.

On the Pi:

```bash
bluetoothctl
```

then type these, one at a time:

```
power on
agent on
default-agent
discoverable on
pairable on
```

Leave that open. On the phone: Settings → Bluetooth → find the Pi → pair.
Confirm the same number appears on both screens. Then type `quit` on the Pi.

### 6.4 Stream

```bash
cd ~/ecg
source .venv/bin/activate
python3 ecg_transmitter.py --record 119 --loop
```

You want `advertising ECGStream on RFCOMM channel 1`.

In the app, clear the `tcp://...` address and type just:

```
raspberry
```

Press **Connect**. Same trace as before, now over Bluetooth.

**This is objective 1 finished.**

### 6b. No Raspberry Pi?

Run the same command on your computer with `--transport tcp --port 9000` and use
the `tcp://` address in the app, as in Step 5. Everything except the Bluetooth
link itself is identical.

---

# STEP 7 — Train the classifier

The system works out of the box using fixed rules. Training fits it to real
labelled data instead and gives you proper results to report.

```
python server\train_classifier.py
```

This downloads about 44 annotated recordings, so it takes 10–30 minutes. Leave
it running.

At the end it prints how well the classifier does on patients it has never seen —
recall, precision and a confusion matrix. **Copy that output somewhere safe**;
those are the numbers for your report.

It also creates `server/model.json`. To make the phone use it too:

```powershell
mkdir "C:\Users\YourName\AndroidStudioProjects\ECGMonitor\app\src\main\assets" -Force
copy "C:\ecg-project\server\model.json" "C:\Users\YourName\AndroidStudioProjects\ECGMonitor\app\src\main\assets\"
```

Rebuild the app, and restart the server so it picks up the new model — it should
say "loaded trained model" instead of "model.json not found".

To see what the classifier actually learned:

```
python tools\print_tree.py
```

---

## What each part of the project does

```
transmitter/ecg_transmitter.py   downloads ECG, converts to 250 Hz, sends it
server/server.py                 receives ECG, finds and labels beats
server/ecg_algorithms.py         the actual signal processing
server/train_classifier.py       fits the classifier to labelled data
android/                         the phone app
tools/cross_check.py             proves phone and server agree
tools/simulate_phone.py          test the server with no phone
tools/diagnose_record.py         check results against doctors' labels
tools/print_tree.py              show what the classifier learned
report/report.tex                the write-up, for Overleaf
ios/                             iPhone version (see ios/README.md)
```

---

## When something goes wrong

| What you see | What it means |
|---|---|
| `Python was not found` | PATH box unticked during install. Reinstall, tick it. |
| `running scripts is disabled` | Run the `Set-ExecutionPolicy` command in Step 1.3. |
| `ModuleNotFoundError` | You forgot `.venv\Scripts\activate` in this terminal. |
| `No such file: requirements.txt` | You are in the wrong folder. `cd` into the project. |
| PhysioNet download hangs | Network blocking it. Try a phone hotspot. |
| `package R does not exist` | Package name is not `com.example.ecg`, or Studio's old starter file is still there. See 4.3. |
| No devices in Android Studio | Try another USB cable. Or use wireless debugging. |
| USB debugging greyed out | Samsung Auto Blocker. Turn it off in Security settings. |
| `Cleartext HTTP not permitted` | The manifest line in Step 4.4 is missing. |
| App hangs on "connecting" | Firewall on port 9000, or wrong address. |
| App says "no paired device" | Pair in the phone's Settings first, not in the app. |
| Every beat says Normal | You are probably using recording 100. Use 119. |
| `cross_check.py` says MISMATCH | The Python and Java were edited differently. Both must match. |

---

## The fastest demo

If you are short on time and just need to show it working:

```
python server\server.py --port 8000
python tools\simulate_phone.py --url http://localhost:8000/api/ecg --record 119
python tools\cross_check.py
```

plus http://localhost:8000/ in a browser. That demonstrates most of the system
in about five minutes with no phone at all.

---

## One thing to be clear about

This is a student project tested against a database of recordings. It has never
been connected to a real person, it has not been clinically validated, and it
must not be used to make decisions about anyone's health.
