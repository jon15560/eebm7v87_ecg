#!/usr/bin/env python3
"""
dump_features.py  --  export per-beat features + cardiologist labels to CSV.

Why this exists: the classifier thresholds have been tuned against a synthetic
signal generator, which has twice failed to predict how the features behave on
real MIT-BIH leads. Guessing thresholds from a handful of printed rows is not
converging. This dumps the actual feature vectors so they can be examined and
fitted directly.

    python3 tools/dump_features.py                    # default record set
    python3 tools/dump_features.py --records 100 106 119 200 208 233
    python3 tools/dump_features.py --all              # all 44 (large)

Writes features.csv in the project root. One row per detected beat that could
be matched to an annotation, with every feature the classifier sees plus the
AAMI class the cardiologist assigned.

The default set is chosen to span the failure modes: clean normal (100, 103),
frequent PVCs (106, 233), bigeminy (119), atrial ectopics (209, 232), and one
notoriously noisy record (228). About 20k beats, roughly 2 MB.
"""

import argparse
import csv
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from ecg_algorithms import (FS, PanTompkins, features, FEATURE_NAMES, LABELS)  # noqa

SYMBOL_TO_CLASS = {}
for s in "NLRej":
    SYMBOL_TO_CLASS[s] = 0
for s in "AaJS":
    SYMBOL_TO_CLASS[s] = 1
for s in "VE":
    SYMBOL_TO_CLASS[s] = 2
for s in "FfQ/":
    SYMBOL_TO_CLASS[s] = 3

DEFAULT = ["100", "103", "106", "119", "200", "208", "209", "228", "232", "233"]
ALL = ["100", "101", "103", "105", "106", "108", "109", "111", "112", "113",
       "114", "115", "116", "117", "118", "119", "121", "122", "123", "124",
       "200", "201", "202", "203", "205", "207", "208", "209", "210", "212",
       "213", "214", "215", "219", "220", "221", "222", "223", "228", "230",
       "231", "232", "233", "234"]

TOL = 0.15


def load(record, db, channel):
    import wfdb
    from scipy.signal import resample_poly
    sig, fields = wfdb.rdsamp(str(record), pn_dir=db)
    ann = wfdb.rdann(str(record), "atr", pn_dir=db)
    fs = fields["fs"]
    x = np.nan_to_num(sig[:, channel])
    g = math.gcd(int(round(fs)), int(FS))
    up, down = int(FS) // g, int(round(fs)) // g
    y = resample_poly(x, up, down)
    return y, ann.sample * (up / down) / FS, ann.symbol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", nargs="*", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--db", default="mitdb")
    ap.add_argument("--channel", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(ROOT, "features.csv"))
    a = ap.parse_args()

    records = ALL if a.all else (a.records or DEFAULT)
    print("dumping %d records -> %s\n" % (len(records), a.out))

    rows = 0
    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["record", "time_s", "truth"] + FEATURE_NAMES)

        for rec in records:
            try:
                sig, ann_t, ann_sym = load(rec, a.db, a.channel)
            except Exception as e:
                print("  %s: skipped (%s)" % (rec, e))
                continue

            det = PanTompkins(FS)
            beats = []
            for v in sig:
                b = det.process(v)
                if b:
                    beats.append(b)
            b = det.flush()
            if b:
                beats.append(b)

            ref = [(t, SYMBOL_TO_CLASS[s]) for t, s in zip(ann_t, ann_sym)
                   if s in SYMBOL_TO_CLASS]
            ref_t = np.array([t for t, _ in ref])
            ref_c = [c for _, c in ref]

            n = 0
            for beat in beats:
                t = beat["index"] / FS
                if not len(ref_t):
                    continue
                j = int(np.argmin(np.abs(ref_t - t)))
                if abs(ref_t[j] - t) > TOL:
                    continue
                f = features(beat)
                w.writerow([rec, round(t, 3), LABELS[ref_c[j]]] +
                           [round(float(v), 6) for v in f])
                n += 1
                rows += 1
            print("  %s: %5d beats" % (rec, n))

    size = os.path.getsize(a.out) / 1024 / 1024
    print("\nwrote %d rows, %.1f MB" % (rows, size))
    print("\nThis file contains no patient-identifying information -- it is")
    print("derived measurements from a public research database.")


if __name__ == "__main__":
    main()
