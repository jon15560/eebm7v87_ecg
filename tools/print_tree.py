#!/usr/bin/env python3
"""
print_tree.py  --  show the trained decision tree in readable form.

model.json is not something to paste into a report: it is arrays of node
indices. What is worth showing is the tree itself, because a depth-3 tree is
small enough to read and check against clinical expectation -- which is a real
advantage over a model whose reasoning cannot be inspected.

    python3 tools/print_tree.py                 # plain text
    python3 tools/print_tree.py --latex         # LaTeX for an appendix

Paste the --latex output into report.tex where you want it.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "server", "model.json")

# Plain-English names, so the printed tree reads as clinical reasoning rather
# than as variable names.
PRETTY = {
    "rr_prev": "gap before beat (s)",
    "rr_next": "gap after beat (s)",
    "rr_ratio": "how early (gap before / average)",
    "rr_next_ratio": "pause after (gap after / average)",
    "width_ratio": "QRS width / normal width",
    "amp_ratio": "height / normal height",
    "corr": "shape similarity to normal",
    "rr_asym": "change in gap",
    "width": "QRS width (s)",
    "zcr_ratio": "zero crossings / normal",
    "area_ratio": "area / normal area",
}


def load():
    if not os.path.exists(MODEL):
        sys.exit("model.json not found. Run: python3 server/train_classifier.py")
    with open(MODEL) as fh:
        m = json.load(fh)
    if m.get("type") != "tree":
        sys.exit("model.json is not a tree (type=%s)" % m.get("type"))
    return m


def walk(m, node, depth, lines, latex):
    feat = m["feature"][node]
    labels = m["labels"]
    if feat < 0:                                    # leaf
        probs = m["value"][node]
        k = probs.index(max(probs))
        pad = "  " * depth
        if latex:
            lines.append(r"%s\textbf{%s} (%.0f\%% confident)\\"
                         % ("\\hspace*{%dem}" % (depth * 2), labels[k], 100 * probs[k]))
        else:
            lines.append("%s-> %s  (%.0f%% confident)" % (pad, labels[k], 100 * probs[k]))
        return

    name = m["features"][feat]
    pretty = PRETTY.get(name, name)
    thr = m["threshold"][node]
    pad = "  " * depth
    if latex:
        lines.append(r"%sif %s $\leq$ %.3f:\\"
                     % ("\\hspace*{%dem}" % (depth * 2), pretty, thr))
    else:
        lines.append("%sif %s <= %.3f:" % (pad, pretty, thr))
    walk(m, m["left"][node], depth + 1, lines, latex)
    if latex:
        lines.append(r"%selse:\\" % ("\\hspace*{%dem}" % (depth * 2)))
    else:
        lines.append("%selse:" % pad)
    walk(m, m["right"][node], depth + 1, lines, latex)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--latex", action="store_true")
    a = ap.parse_args()
    m = load()

    lines = []
    walk(m, 0, 0, lines, a.latex)

    if a.latex:
        print(r"\begin{figure}[!t]")
        print(r"\centering")
        print(r"\fbox{\parbox{0.92\linewidth}{\footnotesize\raggedright")
        for ln in lines:
            print(ln)
        print(r"}}")
        print(r"\caption{The decision tree learned from the training patients."
              r" Its depth is capped at 3 so that it stays readable and can be"
              r" checked against clinical expectation: it splits first on how"
              r" early the beat is, then on how much earlier, and falls back on"
              r" shape for beats that are not early.}")
        print(r"\label{fig:tree}")
        print(r"\end{figure}")
    else:
        n_nodes = len(m["feature"])
        n_leaves = sum(1 for f in m["feature"] if f < 0)
        print("decision tree: %d nodes, %d leaves, trained on records %s\n"
              % (n_nodes, n_leaves, m.get("trained_on", "?")))
        for ln in lines:
            print(ln)
        print("\nFor a report-ready version:  python3 tools/print_tree.py --latex")


if __name__ == "__main__":
    main()
