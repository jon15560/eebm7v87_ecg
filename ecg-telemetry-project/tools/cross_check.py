#!/usr/bin/env python3
"""
cross_check.py  --  prove the phone and the server compute the same thing.

The whole point of porting Pan-Tompkins to Java is that the on-device result
should be trustworthy. The only way to know the port is faithful is to run both
implementations over identical input and diff the output, so this compiles the
Android DSP classes with plain javac (no Android SDK needed - they touch nothing
but java.lang and java.util) and compares every beat, feature and label against
the Python.

    python3 tools/cross_check.py

Run this after touching either implementation. A silent divergence between the
two is the single most likely bug in this project.

The org/json stubs exist only to satisfy the compiler: BeatClassifier imports
org.json, which ships inside Android but not in a desktop JDK. They are never
executed - the check exercises the rule classifier.
"""
import os, subprocess, sys, tempfile, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "android", "app", "src", "main", "java")
HARNESS = os.path.join(ROOT, "tools", "javaharness")
sys.path.insert(0, os.path.join(ROOT, "server"))

from ecg_algorithms import (PanTompkins, features, RuleClassifier, FS,  # noqa: E402
                            load_classifier)


def synth(seconds=60, seed=0):
    import numpy as np
    rng = np.random.default_rng(seed)
    n = int(seconds * FS); t = np.arange(n) / FS; x = np.zeros(n)
    tb, k = 1.0, 0
    while tb < seconds - 1:
        ectopic = (k % 9 == 8)
        lo, hi = max(0, int((tb-.35)*FS)), min(n, int((tb+.45)*FS)); tt = t[lo:hi]-tb
        w = 1.9 if ectopic else 1.0
        q = (-0.15*np.exp(-((tt+0.025*w)/(0.010*w))**2)
             + 1.0*np.exp(-(tt/(0.012*w))**2)
             - 0.25*np.exp(-((tt-0.025*w)/(0.012*w))**2))
        x[lo:hi] += (-0.9*q - 0.4*np.exp(-((tt-0.32)/0.075)**2)) if ectopic \
                    else (q + 0.25*np.exp(-((tt-0.22)/0.045)**2))
        k += 1
        tb += 0.55 if (k % 9 == 8) else (1.05 if ectopic else 0.80)
    return x + 0.05*np.sin(2*np.pi*0.25*t) + 0.012*rng.standard_normal(n)


MODEL = os.path.join(ROOT, "server", "model.json")


def python_beats(sig, clf):
    det, rows = PanTompkins(), []
    def emit(b):
        lab, _ = clf.predict(features(b))
        rows.append("%d %.6f %.6f %.6f %.6f %.6f %s" % (
            b["index"], b["rr_prev"], b["rr_next"], b["width"],
            b["amplitude"], b["corr"], lab))
    for s in sig:
        b = det.process(s)
        if b: emit(b)
    b = det.flush()
    if b: emit(b)
    return rows


def main():
    if not shutil.which("javac"):
        sys.exit("javac not found - install a JDK (apt install default-jdk)")
    sig = synth()

    # Check whichever classifier actually ships: the trained tree if one has
    # been fitted, the rules otherwise. Verifying only the rules would leave the
    # tree evaluator -- the production path -- unchecked on the phone.
    use_model = os.path.exists(MODEL)
    clf = load_classifier(MODEL) if use_model else RuleClassifier()
    print("classifier: %s" % type(clf).__name__)
    py = python_beats(sig, clf)

    tmp = tempfile.mkdtemp(prefix="ecgcheck")
    try:
        out = os.path.join(tmp, "out"); os.makedirs(out)
        srcs = [os.path.join(HARNESS, "org", "json", "JSONArray.java"),
                os.path.join(HARNESS, "org", "json", "JSONObject.java"),
                os.path.join(HARNESS, "CrossCheck.java")]
        for d in ("dsp",):
            p = os.path.join(SRC, "com", "example", "ecg", d)
            srcs += [os.path.join(p, f) for f in os.listdir(p) if f.endswith(".java")]
        subprocess.run(["javac", "-nowarn", "-d", out,
                        "-sourcepath", SRC + os.pathsep + HARNESS] + srcs, check=True)

        inp = "\n".join("%.10f" % v for v in sig) + "\n"
        cmd = ["java", "-cp", out, "CrossCheck"]
        if use_model:
            cmd.append(MODEL)
        r = subprocess.run(cmd, input=inp, capture_output=True, text=True, check=True)
        if r.stderr.strip():
            print(r.stderr.strip())
        ja = [l for l in r.stdout.strip().split("\n") if l]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("python: %d beats   java: %d beats" % (len(py), len(ja)))
    if py == ja:
        print("MATCH - every beat index, feature and label is identical.")
        return 0
    print("MISMATCH:")
    for i, (a, b) in enumerate(zip(py, ja)):
        if a != b:
            print("  beat %d\n    py  %s\n    jav %s" % (i, a, b))
    if len(py) != len(ja):
        print("  differing beat counts")
    return 1


if __name__ == "__main__":
    sys.exit(main())
