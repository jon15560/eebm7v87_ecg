#!/usr/bin/env python3
"""
simulate_phone.py  --  test the server without a phone or a Bluetooth radio.

Does exactly what CloudUploader.java does: reads ECG, chops it into 2-second
batches, POSTs them to /api/ecg at real-time pace, and prints the classifications
that come back.

Use this to get Parts 4 and 5 working BEFORE you touch Android Studio. If the
server is broken, you want to find out here, not while also debugging Bluetooth
pairing and a Gradle build at the same time.

    python3 tools/simulate_phone.py --url http://localhost:8000/api/ecg --record 106
    python3 tools/simulate_phone.py --url http://192.168.1.100:8000/api/ecg --synthetic

--fast skips the real-time pacing and pushes the whole record as quickly as the
server will take it, which is what you want for a quick smoke test.
"""

import argparse
import json
import math
import sys
import time
import urllib.request

FS = 250
BATCH = 500          # 2 s, same as the app


def synth(seconds=60, seed=0):
    import numpy as np
    rng = np.random.default_rng(seed)
    n = int(seconds * FS)
    t = np.arange(n) / FS
    x = np.zeros(n)
    tb, k = 1.0, 0
    while tb < seconds - 1:
        ectopic = (k % 9 == 8)
        lo, hi = max(0, int((tb - .35) * FS)), min(n, int((tb + .45) * FS))
        tt = t[lo:hi] - tb
        w = 1.9 if ectopic else 1.0
        q = (-0.15 * np.exp(-((tt + 0.025 * w) / (0.010 * w)) ** 2)
             + 1.0 * np.exp(-(tt / (0.012 * w)) ** 2)
             - 0.25 * np.exp(-((tt - 0.025 * w) / (0.012 * w)) ** 2))
        x[lo:hi] += ((-0.9 * q - 0.4 * np.exp(-((tt - 0.32) / 0.075) ** 2)) if ectopic
                     else (q + 0.25 * np.exp(-((tt - 0.22) / 0.045) ** 2)))
        k += 1
        tb += 0.55 if (k % 9 == 8) else (1.05 if ectopic else 0.80)
    return list(x + 0.012 * rng.standard_normal(n))


def load(record, db, channel):
    import numpy as np
    import wfdb
    from scipy.signal import resample_poly
    sig, fields = wfdb.rdsamp(str(record), pn_dir=db)
    fs = fields["fs"]
    x = np.nan_to_num(sig[:, channel])
    g = math.gcd(int(round(fs)), FS)
    return list(resample_poly(x, FS // g, int(round(fs)) // g))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000/api/ecg")
    ap.add_argument("--record", default="106")
    ap.add_argument("--db", default="mitdb")
    ap.add_argument("--channel", type=int, default=0)
    ap.add_argument("--device", default="simulated-phone")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--fast", action="store_true", help="no real-time pacing")
    a = ap.parse_args()

    samples = synth(a.seconds) if a.synthetic else load(a.record, a.db, a.channel)
    samples = samples[:int(a.seconds * FS)]
    print("posting %.1f s of ECG to %s" % (len(samples) / FS, a.url))

    totals, seq, t0 = {}, 0, time.monotonic()
    for i in range(0, len(samples) - BATCH + 1, BATCH):
        body = json.dumps({
            "device": a.device, "fs": FS, "seq": seq,
            "samples": [round(float(v), 4) for v in samples[i:i + BATCH]],
        }).encode()
        req = urllib.request.Request(a.url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.load(r)
        except Exception as e:
            print("\nPOST failed: %s" % e)
            print("  Is server.py running, and is the URL reachable from here?")
            return 1

        for b in res.get("beats", []):
            totals[b["label"]] = totals.get(b["label"], 0) + 1
        seq += 1
        flags = "".join(b["label"][0] for b in res.get("beats", []))
        print("\r  batch %3d  hr %3s bpm  server %5.1f ms  beats %-12s"
              % (seq, res.get("hr", "--"), res.get("processing_ms", 0), flags),
              end="", flush=True)

        if not a.fast:
            deadline = t0 + seq * (BATCH / FS)
            d = deadline - time.monotonic()
            if d > 0:
                time.sleep(d)

    print("\n\ntotals: %s" % (totals or "no beats detected"))
    print("open the dashboard to see the trace: %s" % a.url.rsplit("/api/", 1)[0] + "/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
