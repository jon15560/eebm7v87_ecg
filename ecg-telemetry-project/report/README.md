# IEEE report — how to use it

## Overleaf

1. Zip this `report/` folder (or use the project zip).
2. Overleaf → **New Project → Upload Project** → drop the zip in.
3. **Menu → Compiler → pdfLaTeX**, **Main document → `report.tex`**.
4. Recompile. It should build with no errors.

`IEEEtran.cls` is built into Overleaf, so nothing needs installing.

## What you must fill in

| Where | What |
|---|---|
| `\author{...}` near the top | Your name, department, university, email |
| Fig. 1 | Your system block diagram |
| Fig. 4 | Screenshot of the app showing the live waveform (Objectives 2, 3) |
| Fig. 6 | Screenshot of `cross_check.py` reporting MATCH |
| Fig. 7 | Screenshot of the server dashboard (Objectives 4, 5) |
| Fig. 8 | Photo of your hardware setup (Objective 1) |
No results table is left blank — Tables II–V are filled with measured numbers.
Re-run `train_classifier.py` if you want to confirm them on your own machine.

Search the `.tex` for **`SCREENSHOT`** to jump between the placeholders. Each is
a grey box saying what belongs there. To fill one, replace the whole
`\screenshot{...}` call with:

```latex
\includegraphics[width=\linewidth]{figures/my_screenshot.png}
```

and drop the image into `figures/`.

## The four data figures

`fig_pipeline.pdf`, `fig_resample.pdf`, `fig_beats.pdf` and `fig_features.pdf`
are **generated from the real pipeline**, not drawn by hand:

```bash
python3 tools/gen_figures.py
```

Every trace comes from the same code that runs on the server and the phone, so
the figures cannot drift away from the implementation. Re-run this after
changing anything in the DSP.

## Before you submit

- Every number in the report comes from real MIT-BIH data with cardiologist
  labels, tested on patients the classifier never saw. Don't replace them with
  synthetic figures.
- The supraventricular result (2.3%) is poor and is stated plainly in the
  Limitations section. Leave it in. Inventing a better number is worse than
  reporting a bad one, and the reason for it (no P-wave analysis) is a proper
  engineering explanation.
- The report claims the Python and Java versions agree exactly. That's true and
  reproducible, but only if you include the `cross_check.py` output as Fig. 6.
