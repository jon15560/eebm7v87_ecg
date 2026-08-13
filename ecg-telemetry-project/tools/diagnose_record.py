#!/usr/bin/env python3
"""
diagnose_record.py  --  check the detector against the cardiologist annotations
for ONE record and time window.

Use this when the classification looks wrong on real data. It answers the two
questions that matter, separately:

  1. Were there any ectopic beats in the window you actually streamed?
     (A quiet stretch of a record proves nothing.)
  2. If there were, did the detector find them, and did the classifier label
     them correctly?

Confusing those two is the usual reason people conclude their classifier is
broken when it never saw an abnormal beat, or conclude it works when it was
only ever shown normal ones.

    python3 tools/diagnose_record.py --record 106 --seconds 60
    python3 tools/diagnose_record.py --record 106 --start 60 --seconds 120
    python3 tools/diagnose_record.py --record 106 --seconds 0 --verbose
"""

import argparse
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

from ecg_algorithms import (FS, PanTompkins, RuleClassifier, LinearClassifier,  # noqa
                            features, LABELS)

# AAMI EC57 grouping of the MIT-BIH annotation symbols.
SYMBOL_TO_CLASS = {}
for s in "NLRej":
    SYMBOL_TO_CLASS[s] = 0      # normal / bundle branch / nodal escape
for s in "AaJS":
    SYMBOL_TO_CLASS[s] = 1      # supraventricular ectopic
for s in "VE":
    SYMBOL_TO_CLASS[s] = 2      # ventricular ectopic
for s in "FfQ/":
    SYMBOL_TO_CLASS[s] = 3      # fusion / paced / unclassifiable

TOL = 0.15                      # seconds; detection within this = same beat


def load(record, db, channel, start, seconds):
    import wfdb
    from scipy.signal import resample_poly

    sig, fields = wfdb.rdsamp(str(record), pn_dir=db)
    ann = wfdb.rdann(str(record), "atr", pn_dir=db)
    fs = fields["fs"]
    x = np.nan_to_num(sig[:, channel])

    g = math.gcd(int(round(fs)), int(FS))
    up, down = int(FS) // g, int(round(fs)) // g
    y = resample_poly(x, up, down)
    ann_t = ann.sample * (up / down) / FS

    a = int(start * FS)
    b = len(y) if not seconds else min(len(y), a + int(seconds * FS))
    y = y[a:b]
    keep = (ann_t >= start) & (ann_t < start + (b - a) / FS)
    return y, ann_t[keep] - start, [s for s, k in zip(ann.symbol, keep) if k], \
        fields["sig_name"][channel]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", default="106")
    ap.add_argument("--db", default="mitdb")
    ap.add_argument("--channel", type=int, default=0)
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--seconds", type=float, default=60, help="0 = whole record")
    ap.add_argument("--model", default=os.path.join(ROOT, "server", "model.json"))
    ap.add_argument("--verbose", action="store_true", help="list every mismatch")
    a = ap.parse_args()

    sig, ann_t, ann_sym, lead = load(a.record, a.db, a.channel, a.start, a.seconds)
    dur = len(sig) / FS
    print("record %s, lead %s, %.1f s from t=%.0f s\n" % (a.record, lead, dur, a.start))

    # ---- 1. what is actually in this window, per the annotations -----------
    ref = [(t, SYMBOL_TO_CLASS[s]) for t, s in zip(ann_t, ann_sym)
           if s in SYMBOL_TO_CLASS]
    counts = {}
    for s in ann_sym:
        counts[s] = counts.get(s, 0) + 1
    print("ANNOTATIONS IN THIS WINDOW")
    print("  raw symbols: %s" % ", ".join("%s=%d" % kv for kv in sorted(counts.items())))
    per_class = {}
    for _, c in ref:
        per_class[c] = per_class.get(c, 0) + 1
    for k, lab in enumerate(LABELS):
        print("    %-18s %d" % (lab, per_class.get(k, 0)))
    if per_class.get(2, 0) == 0 and per_class.get(1, 0) == 0:
        print("\n  >> No ectopic beats are annotated here. This window CANNOT")
        print("     demonstrate the classifier. Try a different --start, or a")
        print("     different record (106, 119, 208, 233 are PVC-rich).")

    # ---- 2. run the pipeline ----------------------------------------------
    clf = RuleClassifier()
    if os.path.exists(a.model):
        clf = LinearClassifier.load(a.model)
        print("\nusing trained model.json")
    else:
        print("\nusing rule-based classifier (no model.json)")

    det = PanTompkins(FS)
    beats = []
    for v in sig:
        b = det.process(v)
        if b:
            beats.append(b)
    b = det.flush()
    if b:
        beats.append(b)

    ref_t = np.array([t for t, _ in ref])
    ref_c = [c for _, c in ref]

    matched, conf = 0, {}
    rows = []
    for beat in beats:
        t = beat["index"] / FS
        pred = LABELS.index(clf.predict(features(beat))[0])
        if len(ref_t):
            j = int(np.argmin(np.abs(ref_t - t)))
            if abs(ref_t[j] - t) <= TOL:
                matched += 1
                conf[(ref_c[j], pred)] = conf.get((ref_c[j], pred), 0) + 1
                rows.append((t, ref_c[j], pred, beat))
                continue
        conf[(None, pred)] = conf.get((None, pred), 0) + 1

    print("\nDETECTION")
    print("  annotated beats : %d" % len(ref))
    print("  detected beats  : %d" % len(beats))
    print("  matched         : %d" % matched)
    if len(ref):
        # matched can exceed the annotation count when the detector fires on
        # noise near a real beat, so clamp: a sensitivity above 100% is
        # meaningless and was previously printed as one.
        se = min(100.0, 100.0 * matched / len(ref))
        print("  sensitivity     : %.1f%%   (missed %d)"
              % (se, max(0, len(ref) - matched)))
    if len(beats):
        print("  precision       : %.1f%%   (%d false)"
              % (100 * matched / len(beats), len(beats) - matched))

    print("\nCLASSIFICATION (rows = annotation, cols = predicted)")
    print("      " + "".join("%18s" % l for l in LABELS))
    for k, lab in enumerate(LABELS):
        row = [conf.get((k, j), 0) for j in range(len(LABELS))]
        if sum(row) == 0:
            continue
        print("  %-4s" % lab[:4] + "".join("%18d" % v for v in row))
    correct = sum(v for (t, p), v in conf.items() if t is not None and t == p)
    if matched:
        print("\n  accuracy on matched beats: %.1f%%" % (100 * correct / matched))

    # ---- 3. why did the ectopics get missed? -------------------------------
    bad = [r for r in rows if r[1] != r[2] and r[1] in (1, 2)]
    if bad:
        print("\nMISCLASSIFIED ECTOPIC BEATS  (what the features looked like)")
        print("  %8s %6s %6s %8s %8s %8s %8s %8s" %
              ("time", "true", "pred", "rr_ratio", "width_r", "corr", "zcr_r", "area_r"))
        for t, tc, pc, beat in bad[:20 if a.verbose else 8]:
            f = features(beat)
            print("  %8.2f %6s %6s %8.2f %8.2f %8.2f %8.2f %8.2f"
                  % (t, LABELS[tc][:4], LABELS[pc][:4], f[2], f[4], f[6], f[9], f[10]))
        print("\n  Ventricular requires corr < 0.80 AND a shape difference:")
        print("    width_ratio outside 0.85-1.12, area_ratio outside 0.83-1.20,")
        print("    or zcr_ratio outside 0.80-1.25.")
        print("  The shape tests are two-sided on purpose. Measured in the")
        print("  5-15 Hz detection band a PVC comes out NARROWER than a sinus")
        print("  beat, not wider -- the bandpass strips the low frequencies")
        print("  that make it broad. On record 233 annotated PVCs measured")
        print("  width_ratio 0.39-0.74 against a normal of 1.0.")
        print("\n  Hand-set thresholds are a fallback. For the numbers you")
        print("  report, fit them: python3 server/train_classifier.py")


if __name__ == "__main__":
    main()
