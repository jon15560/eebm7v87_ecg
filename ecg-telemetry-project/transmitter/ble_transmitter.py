#!/usr/bin/env python3
"""
ble_transmitter.py  --  Part 1 for iPhone.

iOS gives third-party apps NO access to Bluetooth Classic SPP/RFCOMM; that
profile is reserved for MFi-certified hardware. The only Bluetooth an iPhone app
can use is BLE, through Core Bluetooth. So for the iOS build the transmitter has
to be a BLE *peripheral* exposing a GATT characteristic that the phone subscribes
to, instead of an RFCOMM server.

Everything above the transport is unchanged. The packet format is byte-for-byte
identical to ecg_transmitter.py, and because the receiver is a stream parser that
tolerates arbitrary fragmentation, a packet may be split across several BLE
notifications without the parser noticing.

    pip install bless wfdb scipy numpy
    sudo python3 transmitter/ble_transmitter.py --record 106 --loop

Uses the Nordic UART Service UUIDs, which are the de-facto convention for
"stream of bytes over BLE" and are recognised by nRF Connect and LightBlue --
useful for confirming the transmitter works before writing any Swift.

Requires BlueZ >= 5.43 and root (BLE advertising needs privileges). On a
Raspberry Pi, if advertising fails, enable experimental mode:
    sudo sed -i 's|^ExecStart=.*bluetoothd.*|& --experimental|' \\
        /lib/systemd/system/bluetooth.service
    sudo systemctl daemon-reload && sudo systemctl restart bluetooth
"""

import argparse
import asyncio
import math
import struct
import sys
import time

# Nordic UART Service. TX is from the peripheral's point of view: the phone
# subscribes to it and receives notifications.
SVC_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

TARGET_FS = 250
SYNC0, SYNC1 = 0xA5, 0x5A
SAMPLES_PER_PACKET = 25          # 100 ms, same as the Classic transmitter


def build_packet(seq, samples_uv):
    """Identical wire format to ecg_transmitter.py."""
    body = struct.pack("<HB", seq & 0xFFFF, len(samples_uv))
    body += struct.pack("<%dh" % len(samples_uv), *samples_uv)
    chk = 0
    for b in body:
        chk ^= b
    return bytes([SYNC0, SYNC1]) + body + bytes([chk])


def chunks(data, n):
    """BLE notifications are capped at ATT_MTU-3 bytes. iOS normally negotiates
    an MTU of 185, so a 56-byte packet fits in one notification; but if the MTU
    turns out to be the 23-byte minimum, the packet simply spans several
    notifications and the receiver's stream parser reassembles it. Nothing above
    the transport needs to know."""
    for i in range(0, len(data), n):
        yield data[i:i + n]


# ---------------------------------------------------------------------------
def load_and_resample(record, db, channel, seconds):
    import numpy as np
    import wfdb
    from scipy.signal import resample_poly

    print("downloading %s/%s from PhysioNet ..." % (db, record))
    sig, fields = wfdb.rdsamp(record, pn_dir=db)
    fs = fields["fs"]
    x = np.nan_to_num(sig[:, channel])
    g = math.gcd(int(round(fs)), TARGET_FS)
    up, down = TARGET_FS // g, int(round(fs)) // g
    y = resample_poly(x, up, down)
    print("  resampled %d/%d -> %d samples @ %d Hz" % (up, down, len(y), TARGET_FS))
    return y[:int(seconds * TARGET_FS)] if seconds else y


def synth(seconds):
    import numpy as np
    n = int(seconds * TARGET_FS)
    t = np.arange(n) / TARGET_FS
    x = np.zeros(n)
    tb, k = 1.0, 0
    while tb < seconds:
        early = (k % 9 == 8)
        lo, hi = max(0, int((tb - .3) * TARGET_FS)), min(n, int((tb + .4) * TARGET_FS))
        tt = t[lo:hi] - tb
        w = 1.8 if early else 1.0
        x[lo:hi] += (np.exp(-(tt / (0.012 * w)) ** 2)
                     - 0.25 * np.exp(-((tt - 0.025 * w) / (0.012 * w)) ** 2)
                     + (0 if early else 0.25 * np.exp(-((tt - 0.22) / 0.045) ** 2)))
        k += 1
        tb += 0.55 if (k % 9 == 8) else (1.05 if early else 0.80)
    return x + 0.01 * np.random.randn(n)


# ---------------------------------------------------------------------------
async def run(samples, loop_forever, chunk_size, name):
    from bless import (BlessServer, GATTCharacteristicProperties,
                       GATTAttributePermissions)

    server = BlessServer(name=name)
    await server.add_new_service(SVC_UUID)
    await server.add_new_characteristic(
        SVC_UUID, TX_UUID,
        GATTCharacteristicProperties.notify | GATTCharacteristicProperties.read,
        bytearray(),
        GATTAttributePermissions.readable,
    )
    await server.start()
    print("advertising as \"%s\"" % name)
    print("  service %s" % SVC_UUID)
    print("  notify  %s" % TX_UUID)
    print("\nOpen the iPhone app (or nRF Connect) and connect.")
    print("Streaming starts immediately; notifications are dropped until a")
    print("central subscribes, which is normal.\n")

    char = server.get_characteristic(TX_UUID)
    period = SAMPLES_PER_PACKET / TARGET_FS
    seq, i, sent = 0, 0, 0
    t0 = time.monotonic()

    try:
        while True:
            if i >= len(samples):
                if not loop_forever:
                    break
                i = 0
            block = samples[i:i + SAMPLES_PER_PACKET]
            i += SAMPLES_PER_PACKET
            uv = [max(-32768, min(32767, int(round(float(v) * 1000.0)))) for v in block]
            packet = build_packet(seq, uv)

            for part in chunks(packet, chunk_size):
                char.value = bytearray(part)
                server.update_value(SVC_UUID, TX_UUID)
            seq += 1
            sent += len(uv)

            # Absolute deadline, not sleep(period): sleep overshoots and the
            # error would accumulate into audible drift over a long recording.
            delay = t0 + seq * period - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            if seq % 50 == 0:
                el = time.monotonic() - t0
                print("\r  %6.1f s, %d samples, %.1f Hz effective"
                      % (el, sent, sent / el), end="", flush=True)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        print("\nstopping ...")
        await server.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", default="106")
    ap.add_argument("--db", default="mitdb")
    ap.add_argument("--channel", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=0)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--name", default="ECG-Sim", help="BLE advertised name")
    ap.add_argument("--chunk", type=int, default=180,
                    help="bytes per notification; drop to 20 if the link is flaky")
    a = ap.parse_args()

    samples = synth(a.seconds or 60) if a.synthetic else \
        load_and_resample(a.record, a.db, a.channel, a.seconds)

    try:
        asyncio.run(run(samples, a.loop, a.chunk, a.name))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
