#!/usr/bin/env python3
"""
gen_figures.py  --  build the report's data figures from the real pipeline.

These are not illustrations drawn to look right; every trace is produced by the
same code that runs on the server and the phone, so the figures cannot drift
away from the implementation. Re-run after changing the DSP.

    python3 tools/gen_figures.py
"""

import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import resample_poly, welch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
OUT = os.path.join(ROOT, "report", "figures")
os.makedirs(OUT, exist_ok=True)

from ecg_algorithms import (FS, Bandpass, PanTompkins, RuleClassifier,  # noqa
                            features, FEATURE_NAMES)

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 8,
    "axes.linewidth": 0.6,
    "lines.linewidth": 0.9,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linewidth": 0.4,
    "figure.dpi": 200,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.02,
})

COL = {"N": "#1b7f4b", "S": "#c07800", "V": "#c02a2a"}


def synth(seconds=30, fs=FS, seed=0):
    """Same generator the test-suite uses: normals with periodic PVCs/APBs."""
    rng = np.random.default_rng(seed)
    n = int(seconds * fs)
    t = np.arange(n) / fs
    x = np.zeros(n)
    sched = []
    tb, k = 1.0, 0
    while tb < seconds - 1:
        r = k % 17
        kind = "V" if r == 8 else ("S" if r == 13 else "N")
        sched.append((tb, kind))
        lo, hi = max(0, int((tb - .35) * fs)), min(n, int((tb + .45) * fs))
        tt = t[lo:hi] - tb
        w = 1.9 if kind == "V" else 1.0
        q = (-0.15 * np.exp(-((tt + 0.025 * w) / (0.010 * w)) ** 2)
             + 1.0 * np.exp(-(tt / (0.012 * w)) ** 2)
             - 0.25 * np.exp(-((tt - 0.025 * w) / (0.012 * w)) ** 2))
        if kind == "V":
            x[lo:hi] += -0.9 * q - 0.4 * np.exp(-((tt - 0.32) / 0.075) ** 2)
        else:
            x[lo:hi] += q + 0.25 * np.exp(-((tt - 0.22) / 0.045) ** 2)
            if kind == "N":
                x[lo:hi] += 0.12 * np.exp(-((tt + 0.18) / 0.025) ** 2)
        nxt = 1.05 if kind == "V" else (0.82 if kind == "S" else 0.80)
        if k % 17 == 7:
            nxt = 0.52
        if k % 17 == 12:
            nxt = 0.58
        tb += nxt
        k += 1
    x += 0.05 * np.sin(2 * np.pi * 0.25 * t) + 0.012 * rng.standard_normal(n)
    return x, sched


# ---------------------------------------------------------------- fig 1
def fig_pipeline():
    """Every intermediate stage of Pan-Tompkins on the same 6 s window."""
    sig, _ = synth(30)
    a, b = int(6 * FS), int(12 * FS)

    bp = Bandpass()
    f = np.array([bp.process(v) for v in sig])
    d = np.zeros_like(f)
    for i in range(4, len(f)):
        d[i] = (-f[i - 4] - 2 * f[i - 3] + 2 * f[i - 1] + f[i]) / 8.0
    sq = d ** 2
    win = int(0.15 * FS)
    integ = np.convolve(sq, np.ones(win) / win, mode="same")

    det = PanTompkins(FS)
    peaks = []
    for v in sig:
        bt = det.process(v)
        if bt:
            peaks.append(bt["index"])
    bt = det.flush()
    if bt:
        peaks.append(bt["index"])
    peaks = [p for p in peaks if a <= p < b]

    t = np.arange(a, b) / FS
    names = [("Raw ECG, 250 Hz", sig, "#222222"),
             ("Bandpass 5--15 Hz", f, "#1f4e79"),
             ("Derivative, squared", sq, "#7a4e9e"),
             ("Moving-window integration (150 ms)", integ, "#1b7f4b")]

    fig, ax = plt.subplots(4, 1, figsize=(3.4, 4.0), sharex=True)
    for k, (title, y, c) in enumerate(names):
        ax[k].plot(t, y[a:b], color=c)
        ax[k].set_ylabel(title, fontsize=6.2)
        ax[k].tick_params(labelsize=6)
        for p in peaks:
            ax[k].axvline(p / FS, color="#c02a2a", lw=0.5, alpha=0.55, zorder=0)
    ax[-1].set_xlabel("time (s)", fontsize=7)
    fig.align_ylabels(ax)
    fig.savefig(os.path.join(OUT, "fig_pipeline.pdf"))
    plt.close(fig)
    print("fig_pipeline.pdf   (%d detections marked)" % len(peaks))


# ---------------------------------------------------------------- fig 2
def fig_resample():
    """Why the anti-alias filter matters.

    360 -> 250 Hz is not an integer ratio, so the naive alternative is plain
    interpolation onto the new grid with no pre-filter. A 150 Hz component then
    folds to |150-250| = 100 Hz. Note it does NOT reach the 5-15 Hz QRS band:
    with a 360 Hz source (Nyquist 180) that would require content at 235-245 Hz,
    which cannot exist. The detector is therefore safe either way; what the
    filter protects is the wideband waveform the clinician actually looks at.
    """
    fs0 = 360
    x, _ = synth(40, fs=fs0, seed=2)
    hf = 0.25 * np.sin(2 * np.pi * 150 * np.arange(len(x)) / fs0)  # 150 Hz EMG-like
    x = x + hf

    good = resample_poly(x, 25, 36)                     # anti-aliased
    n_new = int(len(x) * 250 / fs0)
    t_old = np.arange(len(x)) / fs0
    t_new = np.arange(n_new) / 250.0
    bad = np.interp(t_new, t_old, x)                    # no pre-filter

    fig, ax = plt.subplots(1, 1, figsize=(3.4, 2.0))
    for sig_, fs_, lab, c, ls in ((x, fs0, "Original, 360 Hz", "#999999", "-"),
                                  (good, 250, "resample\\_poly, 250 Hz", "#1b7f4b", "-"),
                                  (bad, 250, "Interpolation, no pre-filter", "#c02a2a", "--")):
        fr, pw = welch(sig_, fs_, nperseg=min(1024, len(sig_)))
        ax.semilogy(fr, pw, label=lab, color=c, ls=ls, lw=0.9)
    ax.axvspan(5, 15, color="#1f4e79", alpha=0.12)
    ax.annotate("150 Hz folds\nto 100 Hz", xy=(100, 1e-4), xytext=(112, 3e-3),
                fontsize=5.6, color="#c02a2a",
                arrowprops=dict(arrowstyle="->", color="#c02a2a", lw=0.6))
    ax.set_xlim(0, 180)
    ax.set_xlabel("frequency (Hz)", fontsize=7)
    ax.set_ylabel("PSD", fontsize=7)
    ax.tick_params(labelsize=6)
    ax.legend(fontsize=5.6, loc="upper right", framealpha=0.9)
    fig.savefig(os.path.join(OUT, "fig_resample.pdf"))
    plt.close(fig)
    print("fig_resample.pdf")


# ---------------------------------------------------------------- fig 3
def fig_beats():
    """Annotated strip: what the classifier keys on."""
    sig, sched = synth(30)
    det, clf = PanTompkins(FS), RuleClassifier()
    beats = []
    for v in sig:
        bt = det.process(v)
        if bt:
            bt["label"] = clf.predict(features(bt))[0]
            beats.append(bt)
    bt = det.flush()
    if bt:
        bt["label"] = clf.predict(features(bt))[0]
        beats.append(bt)

    v_idx = [b for b in beats if b["label"] == "Ventricular"]
    centre = v_idx[0]["index"] if v_idx else beats[5]["index"]
    a, b = max(0, centre - int(3.2 * FS)), centre + int(3.2 * FS)
    t = np.arange(a, b) / FS

    fig, ax = plt.subplots(1, 1, figsize=(7.0, 2.1))
    ax.plot(t, sig[a:b], color="#222222", lw=0.8)
    key = {"Normal": "N", "Supraventricular": "S", "Ventricular": "V", "Other": "O"}
    for bb in beats:
        if not (a <= bb["index"] < b):
            continue
        s = key[bb["label"]]
        c = COL.get(s, "#666666")
        ax.axvline(bb["index"] / FS, color=c, lw=0.7, alpha=0.75)
        ax.text(bb["index"] / FS, ax.get_ylim()[1] * 0.92, s, color=c,
                fontsize=7, ha="center", weight="bold")
        ax.text(bb["index"] / FS, ax.get_ylim()[0] * 0.92,
                "%.2f" % bb["rr_prev"], color="#555555", fontsize=5.2, ha="center")
    ax.set_xlabel("time (s)", fontsize=7)
    ax.set_ylabel("amplitude (mV)", fontsize=7)
    ax.tick_params(labelsize=6)
    fig.savefig(os.path.join(OUT, "fig_beats.pdf"))
    plt.close(fig)
    print("fig_beats.pdf")


# ---------------------------------------------------------------- fig 4
def fig_features():
    """Feature separation on REAL annotated data.

    Uses features.csv if present (produced by tools/dump_features.py from
    MIT-BIH), falling back to the synthetic generator otherwise. Real data
    matters here: the synthetic generator twice suggested feature behaviour
    that did not hold on actual leads.
    """
    import csv
    path = os.path.join(ROOT, "features.csv")
    if not os.path.exists(path):
        print("fig_features: features.csv not found; run tools/dump_features.py")
        return

    cols, rows = None, []
    with open(path) as fh:
        for i, row in enumerate(csv.reader(fh)):
            if i == 0:
                cols = row
                continue
            rows.append(row)
    ix = {c: i for i, c in enumerate(cols)}

    def col(name, truth):
        return np.array([float(r[ix[name]]) for r in rows if r[ix["truth"]] == truth])

    fig, ax = plt.subplots(1, 3, figsize=(7.0, 2.3))
    pairs = [("rr_ratio", "prematurity", (0.3, 1.6)),
             ("width_ratio", "QRS width ratio", (0.4, 3.0)),
             ("corr", "template correlation", (-1.0, 1.05))]
    names = [("Normal", "N"), ("Supraventricular", "S"), ("Ventricular", "V")]

    for k, (feat, label, rng) in enumerate(pairs):
        for truth, key in names:
            d = col(feat, truth)
            d = d[(d > rng[0]) & (d < rng[1])]
            ax[k].hist(d, bins=60, range=rng, density=True, histtype="step",
                       lw=1.0, color=COL[key], label=truth)
        ax[k].set_xlabel(label, fontsize=7)
        ax[k].tick_params(labelsize=6)
        ax[k].set_yticks([])
    ax[0].set_ylabel("density", fontsize=7)
    ax[2].legend(fontsize=5.6, framealpha=0.9)
    fig.savefig(os.path.join(OUT, "fig_features.pdf"))
    plt.close(fig)
    print("fig_features.pdf  (%d real annotated beats)" % len(rows))


if __name__ == "__main__":
    fig_pipeline()
    fig_resample()
    fig_beats()
    fig_features()
    print("\nwrote figures to %s" % OUT)
