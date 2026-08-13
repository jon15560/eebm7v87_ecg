#!/usr/bin/env python3
"""
ecg_transmitter.py  --  Part 1 of the assignment.

Downloads a record from PhysioNet, resamples it to exactly 250 Hz, and streams
it over Bluetooth SPP (RFCOMM) in real time, as if it were a chest strap.

    pip install wfdb scipy numpy pybluez2

    # list what is available, then stream record 100 of the MIT-BIH
    # Arrhythmia Database (has plenty of PVCs -- good for testing the classifier)
    python3 ecg_transmitter.py --record 100 --db mitdb

    # no Bluetooth adapter handy?  stream to a TCP socket instead and point
    # the phone at your laptop's IP -- the wire format is identical
    python3 ecg_transmitter.py --record 100 --transport tcp --port 9000

Records worth trying:
    mitdb/100  mostly normal, a few APBs        -- baseline sanity check
    mitdb/106  frequent PVCs                    -- exercises the V class
    mitdb/119  clean bigeminy                   -- every other beat is a PVC
    mitdb/209  many atrial premature beats      -- exercises the S class
"""

import argparse
import math
import socket
import struct
import sys
import time

TARGET_FS = 250

# ---------------------------------------------------------------------------
# Wire format.  Kept deliberately small and self-synchronising: Bluetooth SPP
# is a byte stream with no message boundaries, so the receiver has to be able
# to find the start of a packet after connecting mid-stream or dropping bytes.
#
#   0xA5 0x5A | seq:u16 LE | count:u8 | count * sample:i16 LE (microvolts) | xor:u8
#
# The checksum covers everything from `seq` through the last sample byte.
# ---------------------------------------------------------------------------
SYNC0, SYNC1 = 0xA5, 0x5A
SAMPLES_PER_PACKET = 25            # 100 ms of signal at 250 Hz
MAX_SAMPLES = 64                   # sanity bound on the count byte (see below)


def build_packet(seq, samples_uv):
    body = struct.pack("<HB", seq & 0xFFFF, len(samples_uv))
    body += struct.pack("<%dh" % len(samples_uv), *samples_uv)
    chk = 0
    for b in body:
        chk ^= b
    return bytes([SYNC0, SYNC1]) + body + bytes([chk])


def parse_stream(buf):
    """Reference decoder -- mirrors EcgBluetoothClient.java exactly.
    Returns (list_of_packets, leftover_bytes)."""
    out = []
    i = 0
    while True:
        # hunt for the sync word
        while i + 1 < len(buf) and not (buf[i] == SYNC0 and buf[i + 1] == SYNC1):
            i += 1
        if i + 5 > len(buf):
            break
        count = buf[i + 4]
        # Sanity-check the length byte BEFORE trusting it.  If a corrupted
        # count says "512 samples follow", the decoder would sit waiting for
        # bytes that never come and the stream would stall permanently rather
        # than losing one packet.
        if count == 0 or count > MAX_SAMPLES:
            i += 1
            continue
        total = 2 + 3 + 2 * count + 1
        if i + total > len(buf):
            break
        body = buf[i + 2:i + total - 1]
        chk = 0
        for b in body:
            chk ^= b
        if chk == buf[i + total - 1]:
            seq = struct.unpack_from("<H", body, 0)[0]
            samples = struct.unpack_from("<%dh" % count, body, 3)
            out.append((seq, list(samples)))
            i += total
        else:
            i += 1          # bad checksum: resync one byte along
    return out, buf[i:]


# ---------------------------------------------------------------------------
# Signal source
# ---------------------------------------------------------------------------
def load_and_resample(record, db, channel, seconds):
    import numpy as np
    import wfdb
    from scipy.signal import resample_poly

    print("downloading %s/%s from PhysioNet ..." % (db, record))
    sig, fields = wfdb.rdsamp(record, pn_dir=db)
    fs = fields["fs"]
    x = sig[:, channel]
    x = x[~np.isnan(x)]
    print("  %d samples @ %g Hz, lead %s" % (len(x), fs, fields["sig_name"][channel]))

    # 360 Hz -> 250 Hz is 25/36; resample_poly filters before decimating, so we
    # do not alias the 5-15 Hz QRS energy the detector depends on.
    g = math.gcd(int(round(fs)), TARGET_FS)
    up, down = TARGET_FS // g, int(round(fs)) // g
    y = resample_poly(x, up, down)
    print("  resampled %d/%d -> %d samples @ %d Hz" % (up, down, len(y), TARGET_FS))

    if seconds:
        y = y[:int(seconds * TARGET_FS)]
    return y


def synth(seconds):
    """Fallback so the pipeline can be demonstrated with no internet."""
    import numpy as np
    n = int(seconds * TARGET_FS)
    t = np.arange(n) / TARGET_FS
    x = np.zeros(n)
    rr, tb, k = 0.8, 1.0, 0
    while tb < seconds:
        early = (k % 9 == 8)
        lo, hi = max(0, int((tb - .3) * TARGET_FS)), min(n, int((tb + .4) * TARGET_FS))
        tt = t[lo:hi] - tb
        w = 1.8 if early else 1.0
        x[lo:hi] += (np.exp(-(tt / (0.012 * w)) ** 2)
                     - 0.25 * np.exp(-((tt - 0.025 * w) / (0.012 * w)) ** 2)
                     + (0 if early else 0.25 * np.exp(-((tt - 0.22) / 0.045) ** 2)))
        k += 1
        tb += 0.55 if (k % 9 == 8) else (1.05 if early else rr)
    return x + 0.01 * np.random.randn(n)


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------
def bluetooth_server(port):
    """RFCOMM listener + SDP advert so Android's SPP UUID lookup finds us.

    Needs the device to be discoverable and paired with the phone first:
        bluetoothctl -- power on / discoverable on / pairable on / agent on
    """
    try:
        import bluetooth                      # pybluez2
    except ImportError:
        print("pybluez2 not installed; falling back to a raw RFCOMM socket.\n"
              "  If the phone cannot discover the service, run:\n"
              "    sudo sdptool add --channel=%d SP\n" % port, file=sys.stderr)
        srv = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                            socket.BTPROTO_RFCOMM)
        srv.bind(("", port))
        srv.listen(1)
        print("waiting for a phone on RFCOMM channel %d ..." % port)
        return srv.accept()

    srv = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
    srv.bind(("", bluetooth.PORT_ANY))
    srv.listen(1)
    bluetooth.advertise_service(
        srv, "ECGStream",
        service_id="00001101-0000-1000-8000-00805F9B34FB",
        service_classes=["00001101-0000-1000-8000-00805F9B34FB",
                         bluetooth.SERIAL_PORT_CLASS],
        profiles=[bluetooth.SERIAL_PORT_PROFILE])
    print("advertising ECGStream on RFCOMM channel %d ..." % srv.getsockname()[1])
    return srv.accept()


def tcp_server(port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(1)
    ip = socket.gethostbyname(socket.gethostname())
    print("waiting for a phone on tcp://%s:%d ..." % (ip, port))
    return srv.accept()


# ---------------------------------------------------------------------------
def stream(conn, samples, loop):
    """Paced against a monotonic deadline rather than sleeping a fixed 100 ms:
    time.sleep overshoots, and the error would accumulate into visible drift
    over a few minutes of streaming."""
    period = SAMPLES_PER_PACKET / TARGET_FS
    seq = 0
    i = 0
    t0 = time.monotonic()
    sent = 0
    try:
        while True:
            if i >= len(samples):
                if not loop:
                    break
                i = 0
            chunk = samples[i:i + SAMPLES_PER_PACKET]
            i += SAMPLES_PER_PACKET
            uv = [max(-32768, min(32767, int(round(v * 1000.0)))) for v in chunk]
            conn.sendall(build_packet(seq, uv))
            seq += 1
            sent += len(uv)

            deadline = t0 + seq * period
            delay = deadline - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            if seq % 50 == 0:
                el = time.monotonic() - t0
                print("\r  %6.1f s streamed, %d samples, %.1f Hz effective"
                      % (el, sent, sent / el), end="", flush=True)
    except (BrokenPipeError, ConnectionResetError, OSError):
        print("\nphone disconnected")
    finally:
        conn.close()
    print("\ndone")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", default="100")
    ap.add_argument("--db", default="mitdb")
    ap.add_argument("--channel", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=0, help="0 = whole record")
    ap.add_argument("--transport", choices=["bt", "tcp"], default="bt")
    ap.add_argument("--port", type=int, default=1)
    ap.add_argument("--loop", action="store_true", help="repeat forever")
    ap.add_argument("--synthetic", action="store_true", help="no PhysioNet")
    a = ap.parse_args()

    if a.synthetic:
        samples = synth(a.seconds or 60)
    else:
        samples = load_and_resample(a.record, a.db, a.channel, a.seconds)

    while True:
        conn, addr = (bluetooth_server(a.port) if a.transport == "bt"
                      else tcp_server(a.port))
        print("connected:", addr)
        stream(conn, samples, a.loop)
        if a.transport == "bt":
            break


if __name__ == "__main__":
    main()
