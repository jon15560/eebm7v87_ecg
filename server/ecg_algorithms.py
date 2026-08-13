"""
ecg_algorithms.py
-----------------
Streaming ECG signal processing + beat classification.

Everything in here is written sample-by-sample (no batch/offline tricks) so that
the exact same logic can be transcribed line-for-line into Java and run on the
phone.  See android/.../PanTompkins.java and BeatClassifier.java -- the filter
coefficients and constants are identical.

Pipeline:
    raw @250 Hz
      -> 5-15 Hz Butterworth bandpass (2 cascaded biquads)
      -> 5-point derivative
      -> squaring
      -> 150 ms moving-window integration
      -> adaptive thresholding (Pan & Tompkins 1985) + T-wave discrimination
      -> per-beat feature extraction
      -> multinomial logistic-regression classifier (or rule fallback)

Reference: J. Pan and W. J. Tompkins, "A Real-Time QRS Detection Algorithm",
IEEE Trans. Biomed. Eng., BME-32(3), 1985.
"""

import json
import math

FS = 250.0

# ---------------------------------------------------------------------------
# Biquad bandpass, 5-15 Hz, 2nd order Butterworth per stage, fs = 250 Hz.
# Generated with scipy.signal.butter(2, [5, 15], btype='bandpass', fs=250,
# output='sos').  These constants are duplicated verbatim in PanTompkins.java.
# ---------------------------------------------------------------------------
SOS = [
    # b0, b1, b2, a1, a2   (a0 normalised to 1)
    [0.013359200027856493, 0.026718400055712986, 0.013359200027856493,
     -1.6813538569552235, 0.779892143960428],
    [1.0, -2.0, 1.0,
     -1.8795935958755112, 0.8987098877918257],
]


class Biquad:
    """Transposed direct form II - numerically well behaved, 2 states."""

    def __init__(self, b0, b1, b2, a1, a2):
        self.b0, self.b1, self.b2, self.a1, self.a2 = b0, b1, b2, a1, a2
        self.z1 = 0.0
        self.z2 = 0.0

    def process(self, x):
        y = self.b0 * x + self.z1
        self.z1 = self.b1 * x - self.a1 * y + self.z2
        self.z2 = self.b2 * x - self.a2 * y
        return y


class Bandpass:
    def __init__(self):
        self.stages = [Biquad(*c) for c in SOS]

    def process(self, x):
        for s in self.stages:
            x = s.process(x)
        return x


# ---------------------------------------------------------------------------
# QRS detector
# ---------------------------------------------------------------------------
class PanTompkins:
    """Streaming QRS detector.  Call process(sample) for every sample; it
    returns a Beat dict when a QRS has been confirmed, else None.

    Because the classifier needs the *following* RR interval (to see the
    compensatory pause after an ectopic beat), a beat is only emitted when the
    NEXT beat is found.  So the object is one beat behind real time -- about
    0.8 s of latency, which is fine for a monitor."""

    REFRACTORY = int(0.20 * FS)      # 200 ms - no two QRS closer than this
    TWAVE_WIN = int(0.36 * FS)       # 360 ms - below this, check for T wave
    INT_WIN = int(0.15 * FS)         # 150 ms moving-window integrator
    HIST = int(2.0 * FS)             # circular history of filtered signal
    TEMPLATE_HALF = int(0.08 * FS)   # +-80 ms: the QRS itself, so that a
                                     # neighbouring P/T wave cannot contaminate
                                     # the morphology comparison

    def __init__(self, fs=FS):
        self.fs = fs
        self.bp = Bandpass()

        self.deriv = [0.0] * 5
        self.int_buf = [0.0] * self.INT_WIN
        self.int_idx = 0
        self.int_sum = 0.0

        self.filt_hist = [0.0] * self.HIST   # bandpassed signal
        self.raw_hist = [0.0] * self.HIST
        self.n = 0                           # global sample counter

        # adaptive thresholds
        self.spki = 0.0
        self.npki = 0.0
        self.threshold1 = 0.0
        self.learning = int(2.0 * fs)        # 2 s learning phase

        self.prev_int = 0.0
        self.rising = False
        self.peak_val = 0.0
        self.peak_idx = 0

        self.last_qrs = -10 ** 9
        self.rr_hist = []                    # last 8 RR intervals (samples)
        self.rr_mean = 0.0

        # template of a "normal" beat, updated by exponential averaging
        self.template = None
        self.template_count = 0
        self.width_mean = 0.0        # running mean width of *normal* beats
        self.amp_mean = 0.0          # running mean amplitude of normal beats
        self.zcr_mean = 0.0          # running mean zero-crossing count
        self.area_mean = 0.0         # running mean rectified area

        self.pending = None                  # beat waiting for its next RR

    # -- helpers ----------------------------------------------------------
    def _hist_get(self, buf, idx):
        """idx is a global sample index; returns 0 if it fell out of history."""
        if idx < 0 or idx <= self.n - self.HIST or idx > self.n:
            return 0.0
        return buf[idx % self.HIST]

    def _integrate(self, x):
        self.int_sum -= self.int_buf[self.int_idx]
        self.int_buf[self.int_idx] = x
        self.int_sum += x
        self.int_idx = (self.int_idx + 1) % self.INT_WIN
        return self.int_sum / self.INT_WIN

    # -- main -------------------------------------------------------------
    def process(self, sample):
        """Feed one raw sample (millivolts). Returns a beat dict or None."""
        i = self.n
        f = self.bp.process(sample)
        self.filt_hist[i % self.HIST] = f
        self.raw_hist[i % self.HIST] = sample

        # 5-point derivative: (-x[n-2] -2x[n-1] +2x[n+1] +x[n+2]) / 8
        self.deriv.pop(0)
        self.deriv.append(f)
        d = (-self.deriv[0] - 2 * self.deriv[1] + 2 * self.deriv[3] + self.deriv[4]) / 8.0

        integ = self._integrate(d * d)
        self.n += 1

        # ---- learning phase: just build up threshold statistics ----------
        if i < self.learning:
            if integ > self.spki:
                self.spki = integ
            self.npki = 0.95 * self.npki + 0.05 * integ
            self.threshold1 = self.npki + 0.25 * (self.spki - self.npki)
            self.prev_int = integ
            return None

        # ---- peak picking on the integrated waveform ---------------------
        beat = None
        if integ > self.prev_int:
            self.rising = True
            if integ > self.peak_val:
                self.peak_val = integ
                self.peak_idx = i
        elif self.rising:
            # we just came off a local maximum
            self.rising = False
            beat = self._evaluate_peak(self.peak_val, self.peak_idx)
            self.peak_val = 0.0
        self.prev_int = integ
        return beat

    def _evaluate_peak(self, peak, peak_idx):
        if peak > self.threshold1:
            if peak_idx - self.last_qrs < self.REFRACTORY:
                return None                      # inside refractory period
            if (self.rr_mean > 0 and
                    peak_idx - self.last_qrs < self.TWAVE_WIN and
                    self._is_twave(peak_idx)):
                self.npki = 0.125 * peak + 0.875 * self.npki
                self._update_threshold()
                return None
            self.spki = 0.125 * peak + 0.875 * self.spki
            self._update_threshold()
            return self._register_qrs(peak_idx)
        else:
            self.npki = 0.125 * peak + 0.875 * self.npki
            self._update_threshold()
            return None

    def _update_threshold(self):
        self.threshold1 = self.npki + 0.25 * (self.spki - self.npki)

    def _is_twave(self, peak_idx):
        """T waves are slower than QRS complexes: compare max |slope| of the
        candidate against that of the last accepted QRS."""
        s_new = self._max_slope(peak_idx)
        s_old = self._max_slope(self.last_qrs)
        return s_old > 0 and s_new < 0.5 * s_old

    def _max_slope(self, idx):
        m = 0.0
        for k in range(idx - int(0.05 * self.fs), idx + 1):
            s = abs(self._hist_get(self.filt_hist, k) -
                    self._hist_get(self.filt_hist, k - 1))
            if s > m:
                m = s
        return m

    # -- beat bookkeeping --------------------------------------------------
    def _register_qrs(self, detect_idx):
        # The integrator + derivative delay the signal; the true R peak sits
        # roughly INT_WIN/2 + 2 samples earlier.  Search a window for the
        # largest excursion of the bandpassed signal.
        guess = detect_idx - (self.INT_WIN // 2 + 2)

        # Fiducial point = centroid of QRS energy, found WITHOUT reference to
        # the template.
        #
        # The previous version slid +-45 ms and kept the lag of maximum
        # correlation with the running normal. That made `corr` a best-case
        # similarity score, which flattered exactly the beats that most need to
        # look abnormal: on MIT-BIH record 233, beats a cardiologist labelled
        # ventricular scored corr = 0.83-0.98 against the patient's own normal
        # template, so the "morphologically different" test never fired and
        # two-thirds of PVCs were missed.
        #
        # The energy centroid is a physical definition -- "the middle of this
        # complex" -- that means the same thing for a narrow sinus beat and a
        # broad ectopic one, is immune to the R/S ambiguity that caused the
        # original jitter, and is computed with no knowledge of the template.
        # Correlation measured at that point is then an honest number.
        r_idx = self._centroid(guess)
        r_idx = self._centroid(r_idx)     # one refinement; converges immediately
        corr = self._template_corr(r_idx)

        rr = (r_idx - self.last_qrs) if self.last_qrs > -10 ** 8 else 0
        self.last_qrs = r_idx
        if 0 < rr < 2.0 * self.fs:
            self.rr_hist.append(rr)
            if len(self.rr_hist) > 8:
                self.rr_hist.pop(0)
            self.rr_mean = sum(self.rr_hist) / len(self.rr_hist)

        width = self._qrs_width(r_idx)
        amp = self._amplitude(r_idx)
        zcr = self._zero_crossings(r_idx)
        area = self._area(r_idx)
        beat = {
            "index": r_idx,
            "rr_prev": rr / self.fs if rr else 0.0,
            "rr_mean": self.rr_mean / self.fs if self.rr_mean else 0.0,
            "width": width,
            "amplitude": amp,
            "width_mean": self.width_mean or width,
            "amp_mean": self.amp_mean or amp,
            "zcr": zcr,
            "area": area,
            "zcr_mean": self.zcr_mean or zcr,
            "area_mean": self.area_mean or area,
            "polarity": 1.0 if self._hist_get(self.filt_hist, r_idx) >= 0 else -1.0,
            "corr": corr,
            "rr_next": 0.0,
        }
        self._maybe_update_template(r_idx, beat)

        out, self.pending = self.pending, beat
        if out is not None:
            out["rr_next"] = beat["rr_prev"]
        return out

    def flush(self):
        """Emit the last pending beat (call at end of a recording)."""
        out, self.pending = self.pending, None
        return out

    # -- features ----------------------------------------------------------
    def _qrs_width(self, r_idx):
        """Effective QRS duration as the energy spread about the R peak:

            width = 2 * sqrt( sum f[k]^2 (k-r)^2 / sum f[k]^2 ) / fs

        A threshold-crossing width is bimodal here -- the bandpassed signal
        oscillates, so the crossing search stops at a ringing null on one beat
        and runs into the T wave on the next.  The second moment is smooth,
        threshold-free, and energy-weighted, so the QRS dominates and a
        neighbouring T wave barely moves it."""
        half = int(0.08 * self.fs)
        num = den = 0.0
        for k in range(r_idx - half, r_idx + half + 1):
            v = self._hist_get(self.filt_hist, k)
            e = v * v
            d = k - r_idx
            num += e * d * d
            den += e
        if den <= 0:
            return 0.0
        return 2.0 * math.sqrt(num / den) / self.fs

    def _centroid(self, idx):
        """Energy centre of mass over +-60 ms. Template-free, so it cannot be
        biased by the comparison it is used for."""
        half = int(0.06 * self.fs)
        num = den = 0.0
        for k in range(idx - half, idx + half + 1):
            e = self._hist_get(self.filt_hist, k) ** 2
            num += e * k
            den += e
        if den <= 0:
            return idx
        return int(round(num / den))

    def _zero_crossings(self, r_idx):
        """Sign changes of the bandpassed signal across the QRS. A ventricular
        complex is a slow, smooth deflection; a sinus QRS is a fast biphasic
        one, so it crosses zero more often in the same window."""
        half = int(0.06 * self.fs)
        n = 0
        prev = self._hist_get(self.filt_hist, r_idx - half)
        for k in range(r_idx - half + 1, r_idx + half + 1):
            v = self._hist_get(self.filt_hist, k)
            if (prev < 0) != (v < 0):
                n += 1
            prev = v
        return float(n)

    def _area(self, r_idx):
        """Rectified area of the complex -- a wide beat integrates to more
        even when its peak amplitude is unremarkable."""
        half = int(0.06 * self.fs)
        a = 0.0
        for k in range(r_idx - half, r_idx + half + 1):
            a += abs(self._hist_get(self.filt_hist, k))
        return a

    def _amplitude(self, r_idx):
        lo, hi = 1e18, -1e18
        for k in range(r_idx - int(0.06 * self.fs), r_idx + int(0.06 * self.fs) + 1):
            v = self._hist_get(self.filt_hist, k)
            lo, hi = min(lo, v), max(hi, v)
        return hi - lo

    def _window(self, r_idx):
        h = self.TEMPLATE_HALF
        return [self._hist_get(self.filt_hist, k) for k in range(r_idx - h, r_idx + h + 1)]

    def _template_corr(self, r_idx):
        if self.template is None:
            return 1.0
        return _pearson(self._window(r_idx), self.template)

    def _maybe_update_template(self, r_idx, beat):
        """Only average in beats that look like the running normal, so an
        ectopic run cannot poison the template."""
        w = self._window(r_idx)
        if self.template is None:
            self.template = w
            self.template_count = 1
            self.width_mean = beat["width"]
            self.amp_mean = beat["amplitude"]
            self.zcr_mean = beat["zcr"]
            self.area_mean = beat["area"]
            return
        # Gate on width as well as correlation. With the honest correlation the
        # 0.8 gate alone would still admit a broad ectopic that happens to
        # resemble the normal, and averaging those in corrupts the reference
        # that every other feature is measured against.
        wr = beat["width"] / (self.width_mean or 1e-6)
        if beat["corr"] > 0.7 and wr < 1.15:
            a = 0.9
            self.template = [a * t + (1 - a) * v for t, v in zip(self.template, w)]
            self.width_mean = a * self.width_mean + (1 - a) * beat["width"]
            self.amp_mean = a * self.amp_mean + (1 - a) * beat["amplitude"]
            self.zcr_mean = a * self.zcr_mean + (1 - a) * beat["zcr"]
            self.area_mean = a * self.area_mean + (1 - a) * beat["area"]
            self.template_count += 1


def _pearson(a, b):
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    ma = sum(a[:n]) / n
    mb = sum(b[:n]) / n
    num = sa = sb = 0.0
    for i in range(n):
        da, db = a[i] - ma, b[i] - mb
        num += da * db
        sa += da * da
        sb += db * db
    if sa <= 0 or sb <= 0:
        return 0.0
    return num / math.sqrt(sa * sb)


# ---------------------------------------------------------------------------
# Feature vector -- identical order in Python and Java.
# ---------------------------------------------------------------------------
FEATURE_NAMES = [
    "rr_prev",        # seconds
    "rr_next",        # seconds
    "rr_ratio",       # rr_prev / local mean   (<1 => premature)
    "rr_next_ratio",  # rr_next / local mean   (>1 => compensatory pause)
    "width_ratio",    # width / running normal width  (>1 => wide QRS)
    "amp_ratio",      # amplitude / running normal amplitude
    "corr",           # correlation with running normal template
    "rr_asym",        # (rr_next - rr_prev) / local mean
    "width",          # absolute width, seconds
    "zcr_ratio",      # zero crossings / running normal  (<1 => smooth, slow)
    "area_ratio",     # rectified area / running normal  (>1 => broad complex)
]


def features(beat):
    """Ratios rather than absolutes: the same thresholds then work regardless
    of lead, electrode gain or patient, which absolute mV/ms limits do not."""
    m = beat["rr_mean"] if beat["rr_mean"] > 0 else 0.8
    wm = beat["width_mean"] or 1e-6
    am = beat["amp_mean"] or 1e-6
    return [
        beat["rr_prev"],
        beat["rr_next"],
        beat["rr_prev"] / m,
        beat["rr_next"] / m,
        beat["width"] / wm,
        beat["amplitude"] / am,
        beat["corr"],
        (beat["rr_next"] - beat["rr_prev"]) / m,
        beat["width"],
        beat["zcr"] / (beat["zcr_mean"] or 1e-6),
        beat["area"] / (beat["area_mean"] or 1e-6),
    ]


LABELS = ["Normal", "Supraventricular", "Ventricular", "Other"]


class LinearClassifier:
    """Multinomial logistic regression: standardise, then softmax(Wx + b).
    Model file is plain JSON so the Android app loads the identical weights."""

    def __init__(self, model):
        self.mean = model["mean"]
        self.scale = model["scale"]
        self.W = model["coef"]      # [n_classes][n_features]
        self.b = model["intercept"]
        self.labels = model.get("labels", LABELS)

    @classmethod
    def load(cls, path):
        with open(path) as fh:
            return cls(json.load(fh))

    def predict(self, x):
        z = [(x[i] - self.mean[i]) / (self.scale[i] or 1.0) for i in range(len(x))]
        scores = [sum(w * v for w, v in zip(row, z)) + bias
                  for row, bias in zip(self.W, self.b)]
        mx = max(scores)
        exps = [math.exp(s - mx) for s in scores]
        tot = sum(exps)
        probs = [e / tot for e in exps]
        k = probs.index(max(probs))
        return self.labels[k], probs[k]


class TreeClassifier:
    """Decision tree, evaluated from the JSON written by train_classifier.py.

    A tree rather than the earlier logistic regression because the decision
    boundary here is genuinely non-linear: fitted on 22781 annotated beats with
    leave-one-record-out validation, logistic regression reached 71% accuracy
    against a tree's 77%, and the tree tripled ventricular precision. It is also
    still trivially portable -- a handful of comparisons -- and, unlike a forest,
    readable, so the learned rules can be checked against clinical expectation.
    """

    def __init__(self, model):
        self.feature = model["feature"]        # -1 marks a leaf
        self.threshold = model["threshold"]
        self.left = model["left"]
        self.right = model["right"]
        self.value = model["value"]            # class probabilities per node
        self.labels = model.get("labels", LABELS)

    @classmethod
    def load(cls, path):
        with open(path) as fh:
            return cls(json.load(fh))

    def predict(self, x):
        node = 0
        while self.feature[node] >= 0:
            node = (self.left[node] if x[self.feature[node]] <= self.threshold[node]
                    else self.right[node])
        probs = self.value[node]
        k = probs.index(max(probs))
        return self.labels[k], probs[k]


def load_classifier(path):
    """Pick the right evaluator for whatever model.json contains."""
    with open(path) as fh:
        model = json.load(fh)
    kind = model.get("type", "linear")
    if kind == "tree":
        return TreeClassifier(model)
    return LinearClassifier(model)


class RuleClassifier:
    """Fallback used when no trained model.json is present.  Encodes the
    textbook criteria: PVCs are wide, early and morphologically different;
    APBs are early but look normal; long pauses are escape/dropped beats."""

    labels = LABELS

    def predict(self, x):
        (rr_prev, rr_next, rr_ratio, rr_next_ratio,
         width_ratio, amp_ratio, corr, rr_asym, width,
         zcr_ratio, area_ratio) = x

        if amp_ratio < 0.25 or width_ratio > 3.5:
            return "Other", 0.6                       # noise / lead-off
        premature = rr_ratio < 0.85
        compensatory = rr_next_ratio > 1.10

        # A ventricular beat is defined by its ORIGIN, which shows up as an
        # abnormal activation path: the complex must be both morphologically
        # unlike the patient's normal AND broader than it.  Requiring both
        # keeps ordinary beats sitting next to an ectopic -- whose correlation
        # window is contaminated by the neighbour -- out of this class.
        # A supraventricular ectopic travels the normal His-Purkinje route, so
        # it is early but still narrow.
        # The classic bedside discriminator between a PVC and an atrial
        # premature beat is the pause that follows: a PVC does not reset the
        # sinus node, so the next sinus beat arrives on schedule and the pause
        # is fully compensatory.  An atrial ectopic resets it, giving a shorter
        # pause.  Morphology + pause together are more reliable than either.
        full_pause = rr_next_ratio > 1.20

        # Morphology score combining three independent views of "this complex
        # was not conducted normally": it is broader, it is shaped differently,
        # and it is a slower/smoother deflection. Any one of them alone is
        # noisy on real leads -- width_ratio was the only feature carrying real
        # signal before, and it cannot separate the classes by itself.
        # Abnormal morphology is NECESSARY, not merely one vote. A beat that
        # still matches the patient's own template is, by definition, being
        # conducted the normal way -- whatever its width measurement says.
        # Width alone was tried as a sufficient condition and flagged large
        # numbers of ordinary beats whose measurement window was contaminated
        # by a neighbouring complex.
        different = corr < 0.80

        # Shape difference is DIRECTION-AGNOSTIC, which is not obvious.
        # Measured in the 5-15 Hz detection band, a ventricular complex comes
        # out NARROWER than a sinus one, not wider: the bandpass strips the
        # low-frequency content that makes a PVC broad, collapsing it into a
        # single smooth concentrated hump, while a sinus QRS keeps its sharp
        # biphasic R-S spread. On MIT-BIH record 233 annotated PVCs measured
        # width_ratio 0.39-0.74 against a normal of 1.0. The feature separates
        # the classes strongly; an earlier version simply had the inequality
        # the wrong way round and tested only for widening.
        reshaped = not (0.85 <= width_ratio <= 1.12)
        requantified = not (0.83 <= area_ratio <= 1.20)
        slow = zcr_ratio < 0.80 or zcr_ratio > 1.25

        ventricular = different and (reshaped or requantified or slow)

        if ventricular:
            return "Ventricular", 0.9 if (premature and compensatory) else 0.7
        if premature:
            return "Supraventricular", 0.8 if compensatory else 0.65
        if rr_ratio > 1.6:
            return "Other", 0.5                       # dropped / escape beat
        return "Normal", 0.9


def analyse(samples, fs=FS, classifier=None, detector=None):
    """Convenience wrapper: run the whole chain over a block of samples.
    Pass a persistent `detector` to keep state across streamed blocks."""
    det = detector or PanTompkins(fs)
    clf = classifier or RuleClassifier()
    out = []
    for s in samples:
        beat = det.process(s)
        if beat:
            label, conf = clf.predict(features(beat))
            beat["label"] = label
            beat["confidence"] = conf
            out.append(beat)
    return out, det
