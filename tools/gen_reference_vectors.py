#!/usr/bin/env python3
"""
gen_reference_vectors.py  --  freeze the Python DSP output for the Swift tests.

The Android port is verified by compiling the Java with javac and diffing it
against Python directly (tools/cross_check.py). Swift cannot be compiled outside
Xcode, so the equivalence check is inverted instead: the Python implementation's
exact output is frozen here, and ios/ECGMonitorTests asserts the Swift port
reproduces it on the Mac.

    python3 tools/gen_reference_vectors.py

Re-run after ANY change to ecg_algorithms.py, then re-run the Xcode tests. If
you change the DSP and not the vectors, the test will fail for the right reason.
"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from ecg_algorithms import PanTompkins, features, RuleClassifier, FS  # noqa
from gen_figures import synth                                          # noqa

OUT = os.path.join(ROOT, "ios", "ECGMonitor", "Resources", "reference_vectors.json")


def main(seconds=30):
    sig, _ = synth(seconds)          # gen_figures.synth returns (signal, schedule)
    sig = sig[:int(seconds * FS)]

    # Round BEFORE detecting, not after. The JSON stores samples to 8 decimals,
    # so Swift reads the rounded values; computing the expected beats from the
    # unrounded signal would mean the test compares against an input the Swift
    # side never sees. It happens to make no difference here, but relying on
    # that is luck rather than design.
    sig = [round(float(v), 8) for v in sig]

    det, clf = PanTompkins(FS), RuleClassifier()
    beats = []

    def emit(b):
        label, _ = clf.predict(features(b))
        beats.append({
            "index": b["index"],
            "rr_prev": round(b["rr_prev"], 6),
            "rr_next": round(b["rr_next"], 6),
            "width": round(b["width"], 6),
            "amplitude": round(float(b["amplitude"]), 6),
            "corr": round(float(b["corr"]), 6),
            "label": label,
        })

    for s in sig:
        b = det.process(s)
        if b:
            emit(b)
    b = det.flush()
    if b:
        emit(b)

    doc = {
        "description": "Reference output of server/ecg_algorithms.py. The Swift "
                       "port must reproduce these beats exactly.",
        "fs": FS,
        "tolerance": 1e-6,
        "samples": sig,
        "beats": beats,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(doc, fh)

    counts = {}
    for b in beats:
        counts[b["label"]] = counts.get(b["label"], 0) + 1
    print("wrote %s" % OUT)
    print("  %d samples (%.0f s), %d beats: %s"
          % (len(sig), seconds, len(beats), counts))
    print("  %.0f KB" % (os.path.getsize(OUT) / 1024))


if __name__ == "__main__":
    main()
