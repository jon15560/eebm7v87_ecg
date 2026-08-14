package com.example.ecg.dsp;

/**
 * Streaming QRS detector - the "even more challenging" part of the assignment,
 * running the whole analysis on the phone instead of the server.
 *
 * This is a line-for-line port of server/ecg_algorithms.py. The filter
 * coefficients, window lengths and thresholds are identical, so the phone and
 * the server produce the same beats from the same signal. If you change one,
 * change the other, and re-run tools/cross_check.py.
 *
 * Cost is about 40 floating point ops per sample; at 250 Hz that is nothing
 * for any Android device made this decade. Call process() from the Bluetooth
 * reader thread, never from the UI thread.
 *
 * Reference: J. Pan and W. J. Tompkins, "A Real-Time QRS Detection Algorithm",
 * IEEE Trans. Biomed. Eng., BME-32(3), pp. 230-236, 1985.
 */
public class PanTompkins {

    /** One detected heartbeat plus the features the classifier needs. */
    public static class Beat {
        public int index;          // sample number of the R peak
        public double rrPrev;      // seconds to the previous R peak
        public double rrNext;      // seconds to the next R peak
        public double rrMean;      // mean of the last 8 RR intervals
        public double width;       // effective QRS duration, seconds
        public double amplitude;   // peak-to-peak of the bandpassed QRS
        public double widthMean;   // running mean width of normal beats
        public double ampMean;     // running mean amplitude of normal beats
        public double corr;        // correlation with the running normal template
        public double zcr;         // zero crossings across the QRS
        public double area;        // rectified area of the QRS
        public double zcrMean;     // running mean zero crossings of normal beats
        public double areaMean;    // running mean area of normal beats
        public String label = "?";
        public double confidence;

        public double bpm() { return rrPrev > 0 ? 60.0 / rrPrev : 0; }
    }

    // 5-15 Hz Butterworth bandpass, 2 cascaded biquads, fs = 250 Hz.
    // scipy.signal.butter(2, [5,15], btype='bandpass', fs=250, output='sos')
    private static final double[][] SOS = {
        {0.013359200027856493, 0.026718400055712986, 0.013359200027856493,
         -1.6813538569552235, 0.779892143960428},
        {1.0, -2.0, 1.0,
         -1.8795935958755112, 0.8987098877918257},
    };

    private static final class Biquad {
        final double b0, b1, b2, a1, a2;
        double z1, z2;
        Biquad(double[] c) { b0=c[0]; b1=c[1]; b2=c[2]; a1=c[3]; a2=c[4]; }
        double process(double x) {
            double y = b0 * x + z1;
            z1 = b1 * x - a1 * y + z2;
            z2 = b2 * x - a2 * y;
            return y;
        }
    }

    private final double fs;
    private final Biquad[] stages = new Biquad[SOS.length];

    private final int refractory, tWaveWin, intWin, hist, templateHalf;

    private final double[] deriv = new double[5];
    private final double[] intBuf;
    private int intIdx = 0;
    private double intSum = 0;

    private final double[] filtHist;
    private int n = 0;

    private double spki = 0, npki = 0, threshold1 = 0;
    private final int learning;

    private double prevInt = 0;
    private boolean rising = false;
    private double peakVal = 0;
    private int peakIdx = 0;

    private static final int NO_QRS = Integer.MIN_VALUE / 2;
    private int lastQrs = NO_QRS;
    private final double[] rrHist = new double[8];
    private int rrCount = 0;
    private double rrMean = 0;

    private double[] template = null;
    private double widthMean = 0, ampMean = 0, zcrMean = 0, areaMean = 0;

    private Beat pending = null;

    public PanTompkins(double sampleRate) {
        this.fs = sampleRate;
        for (int i = 0; i < SOS.length; i++) stages[i] = new Biquad(SOS[i]);
        refractory   = (int) (0.20 * fs);
        tWaveWin     = (int) (0.36 * fs);
        intWin       = (int) (0.15 * fs);
        hist         = (int) (2.0 * fs);
        templateHalf = (int) (0.08 * fs);
        learning     = (int) (2.0 * fs);
        intBuf   = new double[intWin];
        filtHist = new double[hist];
    }

    /** Feed one sample in millivolts. Returns a finished Beat, or null. */
    public Beat process(double sample) {
        int i = n;
        double f = sample;
        for (Biquad s : stages) f = s.process(f);
        filtHist[Math.floorMod(i, hist)] = f;

        System.arraycopy(deriv, 1, deriv, 0, 4);
        deriv[4] = f;
        double d = (-deriv[0] - 2 * deriv[1] + 2 * deriv[3] + deriv[4]) / 8.0;

        intSum -= intBuf[intIdx];
        intBuf[intIdx] = d * d;
        intSum += intBuf[intIdx];
        intIdx = (intIdx + 1) % intWin;
        double integ = intSum / intWin;

        n++;

        if (i < learning) {
            if (integ > spki) spki = integ;
            npki = 0.95 * npki + 0.05 * integ;
            threshold1 = npki + 0.25 * (spki - npki);
            prevInt = integ;
            return null;
        }

        Beat beat = null;
        if (integ > prevInt) {
            rising = true;
            if (integ > peakVal) { peakVal = integ; peakIdx = i; }
        } else if (rising) {
            rising = false;
            beat = evaluatePeak(peakVal, peakIdx);
            peakVal = 0;
        }
        prevInt = integ;
        return beat;
    }

    /** Emit the final buffered beat when the stream ends. */
    public Beat flush() { Beat b = pending; pending = null; return b; }

    public double heartRate() { return rrMean > 0 ? 60.0 / (rrMean / fs) : 0; }

    // ------------------------------------------------------------------
    private double histGet(double[] buf, int idx) {
        if (idx < 0 || idx <= n - hist || idx > n) return 0.0;
        return buf[Math.floorMod(idx, hist)];
    }

    private Beat evaluatePeak(double peak, int idx) {
        if (peak > threshold1) {
            if (idx - lastQrs < refractory) return null;
            if (rrMean > 0 && idx - lastQrs < tWaveWin && isTWave(idx)) {
                npki = 0.125 * peak + 0.875 * npki;
                threshold1 = npki + 0.25 * (spki - npki);
                return null;
            }
            spki = 0.125 * peak + 0.875 * spki;
            threshold1 = npki + 0.25 * (spki - npki);
            return registerQrs(idx);
        }
        npki = 0.125 * peak + 0.875 * npki;
        threshold1 = npki + 0.25 * (spki - npki);
        return null;
    }

    /** T waves rise more slowly than QRS complexes. */
    private boolean isTWave(int idx) {
        double sNew = maxSlope(idx), sOld = maxSlope(lastQrs);
        return sOld > 0 && sNew < 0.5 * sOld;
    }

    private double maxSlope(int idx) {
        double m = 0;
        for (int k = idx - (int) (0.05 * fs); k <= idx; k++) {
            double s = Math.abs(histGet(filtHist, k) - histGet(filtHist, k - 1));
            if (s > m) m = s;
        }
        return m;
    }

    private Beat registerQrs(int detectIdx) {
        // Fiducial point = centroid of QRS energy, found WITHOUT reference to
        // the template.
        //
        // The previous version slid +-45 ms and kept the lag of maximum
        // correlation with the running normal. That made `corr` a best-case
        // similarity score, flattering exactly the beats that most need to look
        // abnormal: on MIT-BIH record 233, beats annotated ventricular scored
        // corr = 0.83-0.98 against the patient's own normal template, so the
        // "morphologically different" test never fired and two thirds of PVCs
        // were missed.
        //
        // The energy centroid is a physical definition -- "the middle of this
        // complex" -- meaning the same thing for a narrow sinus beat and a broad
        // ectopic one, immune to the R/S ambiguity that caused the original
        // jitter, and computed with no knowledge of the template.
        int guessIdx = detectIdx - (intWin / 2 + 2);
        int rIdx = centroid(centroid(guessIdx));
        double corr = templateCorr(rIdx);

        int rr = (lastQrs > NO_QRS) ? rIdx - lastQrs : 0;
        lastQrs = rIdx;
        if (rr > 0 && rr < 2.0 * fs) {
            if (rrCount < rrHist.length) {
                rrHist[rrCount++] = rr;
            } else {
                System.arraycopy(rrHist, 1, rrHist, 0, rrHist.length - 1);
                rrHist[rrHist.length - 1] = rr;
            }
            double s = 0;
            for (int k = 0; k < rrCount; k++) s += rrHist[k];
            rrMean = s / rrCount;
        }

        Beat b = new Beat();
        b.index = rIdx;
        b.rrPrev = rr > 0 ? rr / fs : 0.0;
        b.rrMean = rrMean > 0 ? rrMean / fs : 0.0;
        b.width = qrsWidth(rIdx);
        b.amplitude = amplitude(rIdx);
        b.zcr = zeroCrossings(rIdx);
        b.area = area(rIdx);
        b.corr = corr;
        b.widthMean = widthMean != 0 ? widthMean : b.width;
        b.ampMean = ampMean != 0 ? ampMean : b.amplitude;
        b.zcrMean = zcrMean != 0 ? zcrMean : b.zcr;
        b.areaMean = areaMean != 0 ? areaMean : b.area;
        b.rrNext = 0.0;

        updateTemplate(rIdx, b);

        // Hold each beat back one cycle so the classifier can see the pause
        // that FOLLOWS it - the compensatory pause is what separates a PVC
        // from an atrial premature beat.
        Beat out = pending;
        pending = b;
        if (out != null) out.rrNext = b.rrPrev;
        return out;
    }

    /**
     * Effective QRS duration as the energy spread about R:
     *   width = 2*sqrt( sum f[k]^2 (k-r)^2 / sum f[k]^2 ) / fs
     * A threshold-crossing width is bimodal here because the bandpassed signal
     * rings; the second moment is smooth and threshold-free.
     */
    private double qrsWidth(int rIdx) {
        int half = (int) (0.08 * fs);
        double num = 0, den = 0;
        for (int k = rIdx - half; k <= rIdx + half; k++) {
            double v = histGet(filtHist, k), e = v * v, dd = k - rIdx;
            num += e * dd * dd;
            den += e;
        }
        if (den <= 0) return 0;
        return 2.0 * Math.sqrt(num / den) / fs;
    }

    /** Energy centre of mass over +-60 ms. Template-free, so it cannot be
     *  biased by the comparison it is used for. */
    private int centroid(int idx) {
        int half = (int) (0.06 * fs);
        double num = 0, den = 0;
        for (int k = idx - half; k <= idx + half; k++) {
            double e = histGet(filtHist, k);
            e = e * e;
            num += e * k;
            den += e;
        }
        if (den <= 0) return idx;
        return (int) Math.round(num / den);
    }

    /** Sign changes across the QRS. A ventricular complex is a slow, smooth
     *  deflection; a sinus QRS is a fast biphasic one, so it crosses zero more
     *  often in the same window. */
    private double zeroCrossings(int rIdx) {
        int half = (int) (0.06 * fs);
        int n = 0;
        double prev = histGet(filtHist, rIdx - half);
        for (int k = rIdx - half + 1; k <= rIdx + half; k++) {
            double v = histGet(filtHist, k);
            if ((prev < 0) != (v < 0)) n++;
            prev = v;
        }
        return n;
    }

    /** Rectified area -- a wide beat integrates to more even when its peak
     *  amplitude is unremarkable. */
    private double area(int rIdx) {
        int half = (int) (0.06 * fs);
        double a = 0;
        for (int k = rIdx - half; k <= rIdx + half; k++) a += Math.abs(histGet(filtHist, k));
        return a;
    }

    private double templateCorr(int rIdx) {
        if (template == null) return 1.0;
        double[] w = new double[2 * templateHalf + 1];
        fillWindow(rIdx, w);
        return pearson(w, template);
    }

    private double amplitude(int rIdx) {
        double lo = 1e18, hi = -1e18;
        for (int k = rIdx - (int) (0.06 * fs); k <= rIdx + (int) (0.06 * fs); k++) {
            double v = histGet(filtHist, k);
            if (v < lo) lo = v;
            if (v > hi) hi = v;
        }
        return hi - lo;
    }

    private void fillWindow(int rIdx, double[] out) {
        for (int j = 0; j < out.length; j++) {
            out[j] = histGet(filtHist, rIdx - templateHalf + j);
        }
    }

    /** Only average in beats that still look normal, so a run of ectopics
     *  cannot drag the template towards the abnormal morphology. */
    private void updateTemplate(int rIdx, Beat b) {
        double[] w = new double[2 * templateHalf + 1];
        fillWindow(rIdx, w);
        if (template == null) {
            template = w;
            widthMean = b.width;
            ampMean = b.amplitude;
            zcrMean = b.zcr;
            areaMean = b.area;
            return;
        }
        // Gate on width as well as correlation: with the honest correlation the
        // 0.8 gate alone would still admit a broad ectopic that happens to
        // resemble the normal, and averaging those in corrupts the reference
        // every other feature is measured against.
        double wr = b.width / (widthMean != 0 ? widthMean : 1e-6);
        if (b.corr > 0.7 && wr < 1.15) {
            final double a = 0.9;
            for (int j = 0; j < template.length; j++) {
                template[j] = a * template[j] + (1 - a) * w[j];
            }
            widthMean = a * widthMean + (1 - a) * b.width;
            ampMean = a * ampMean + (1 - a) * b.amplitude;
            zcrMean = a * zcrMean + (1 - a) * b.zcr;
            areaMean = a * areaMean + (1 - a) * b.area;
        }
    }

    static double pearson(double[] a, double[] b) {
        int m = Math.min(a.length, b.length);
        if (m == 0) return 0;
        double ma = 0, mb = 0;
        for (int i = 0; i < m; i++) { ma += a[i]; mb += b[i]; }
        ma /= m; mb /= m;
        double num = 0, sa = 0, sb = 0;
        for (int i = 0; i < m; i++) {
            double da = a[i] - ma, db = b[i] - mb;
            num += da * db; sa += da * da; sb += db * db;
        }
        if (sa <= 0 || sb <= 0) return 0;
        return num / Math.sqrt(sa * sb);
    }
}
