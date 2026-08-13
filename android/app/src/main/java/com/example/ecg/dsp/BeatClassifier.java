package com.example.ecg.dsp;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Beat classifier - the port of RuleClassifier / LinearClassifier from
 * server/ecg_algorithms.py.
 *
 * Two modes:
 *   - RULES: encodes the textbook criteria, works with no training data.
 *   - TRAINED: multinomial logistic regression whose weights were fitted on the
 *     MIT-BIH Arrhythmia Database by server/train_classifier.py and dropped into
 *     app/src/main/assets/model.json. Exactly the same weights the server uses.
 *
 * Features are RATIOS against the patient's own running normal beat rather than
 * absolute millivolts and milliseconds. That way one set of thresholds works
 * across leads, electrode placements and patients, which absolute limits do not.
 */
public class BeatClassifier {

    public static final String[] LABELS =
        {"Normal", "Supraventricular", "Ventricular", "Other"};

    private double[] mean, scale, intercept;
    private double[][] coef;
    private boolean trained = false;

    // Decision tree, as written by train_classifier.py. Arrays are parallel and
    // indexed by node; feature[n] < 0 marks a leaf.
    private int[] tFeature, tLeft, tRight;
    private double[] tThreshold;
    private double[][] tValue;
    private boolean isTree = false;

    public BeatClassifier() { }

    /** Load weights exported by train_classifier.py. Falls back to rules on error. */
    public BeatClassifier(String modelJson) {
        try {
            JSONObject o = new JSONObject(modelJson);
            if ("tree".equals(o.optString("type", "linear"))) {
                tFeature = toIntArray(o.getJSONArray("feature"));
                tLeft = toIntArray(o.getJSONArray("left"));
                tRight = toIntArray(o.getJSONArray("right"));
                tThreshold = toArray(o.getJSONArray("threshold"));
                JSONArray v = o.getJSONArray("value");
                tValue = new double[v.length()][];
                for (int i = 0; i < v.length(); i++) tValue[i] = toArray(v.getJSONArray(i));
                isTree = true;
                trained = true;
                return;
            }
            mean = toArray(o.getJSONArray("mean"));
            scale = toArray(o.getJSONArray("scale"));
            intercept = toArray(o.getJSONArray("intercept"));
            JSONArray c = o.getJSONArray("coef");
            coef = new double[c.length()][];
            for (int i = 0; i < c.length(); i++) coef[i] = toArray(c.getJSONArray(i));
            trained = true;
        } catch (Exception e) {
            trained = false;   // model.json missing or malformed -> use rules
        }
    }

    public boolean isTrained() { return trained; }

    private static int[] toIntArray(JSONArray a) {
        int[] r = new int[a.length()];
        for (int i = 0; i < a.length(); i++) r[i] = a.optInt(i, -1);
        return r;
    }

    private static double[] toArray(JSONArray a) {
        double[] r = new double[a.length()];
        for (int i = 0; i < a.length(); i++) r[i] = a.optDouble(i, 0);
        return r;
    }

    /** Feature order must match FEATURE_NAMES in ecg_algorithms.py. */
    public static double[] features(PanTompkins.Beat b) {
        double m = b.rrMean > 0 ? b.rrMean : 0.8;
        double wm = b.widthMean != 0 ? b.widthMean : 1e-6;
        double am = b.ampMean != 0 ? b.ampMean : 1e-6;
        return new double[] {
            b.rrPrev,
            b.rrNext,
            b.rrPrev / m,
            b.rrNext / m,
            b.width / wm,
            b.amplitude / am,
            b.corr,
            (b.rrNext - b.rrPrev) / m,
            b.width,
            b.zcr / (b.zcrMean != 0 ? b.zcrMean : 1e-6),
            b.area / (b.areaMean != 0 ? b.areaMean : 1e-6),
        };
    }

    /** Classifies in place: sets beat.label and beat.confidence. */
    public void classify(PanTompkins.Beat b) {
        double[] x = features(b);
        if (isTree) predictTree(b, x);
        else if (trained) predictLinear(b, x);
        else predictRules(b, x);
    }

    /** Walk the tree: left when the feature is <= threshold, matching
     *  scikit-learn's convention exactly. */
    private void predictTree(PanTompkins.Beat b, double[] x) {
        int node = 0;
        while (tFeature[node] >= 0) {
            node = (x[tFeature[node]] <= tThreshold[node]) ? tLeft[node] : tRight[node];
        }
        double[] probs = tValue[node];
        int best = 0;
        for (int k = 1; k < probs.length; k++) if (probs[k] > probs[best]) best = k;
        b.label = LABELS[Math.min(best, LABELS.length - 1)];
        b.confidence = probs[best];
    }

    private void predictLinear(PanTompkins.Beat b, double[] x) {
        double[] z = new double[x.length];
        for (int i = 0; i < x.length; i++) {
            double s = (i < scale.length && scale[i] != 0) ? scale[i] : 1.0;
            z[i] = (x[i] - (i < mean.length ? mean[i] : 0)) / s;
        }
        double[] scores = new double[coef.length];
        double mx = -Double.MAX_VALUE;
        for (int k = 0; k < coef.length; k++) {
            double s = intercept[k];
            for (int i = 0; i < z.length && i < coef[k].length; i++) s += coef[k][i] * z[i];
            scores[k] = s;
            if (s > mx) mx = s;
        }
        double tot = 0;
        for (int k = 0; k < scores.length; k++) { scores[k] = Math.exp(scores[k] - mx); tot += scores[k]; }
        int best = 0;
        for (int k = 1; k < scores.length; k++) if (scores[k] > scores[best]) best = k;
        b.label = LABELS[Math.min(best, LABELS.length - 1)];
        b.confidence = scores[best] / tot;
    }

    private void predictRules(PanTompkins.Beat b, double[] x) {
        double rrRatio = x[2], rrNextRatio = x[3];
        double widthRatio = x[4], ampRatio = x[5], corr = x[6];
        double zcrRatio = x[9], areaRatio = x[10];

        if (ampRatio < 0.25 || widthRatio > 3.5) { set(b, "Other", 0.6); return; }

        boolean premature = rrRatio < 0.85;
        boolean compensatory = rrNextRatio > 1.10;

        // The bedside discriminator between a PVC and an atrial premature beat
        // is the pause that follows. A PVC does not reset the sinus node, so
        // the next sinus beat arrives on schedule and the pause is fully
        // compensatory; an atrial ectopic resets it and the pause is shorter.
        boolean fullPause = rrNextRatio > 1.20;

        // Abnormal morphology is NECESSARY, not merely one vote. A beat that
        // still matches the patient's own template is by definition being
        // conducted the normal way, whatever its width measurement says. Width
        // alone was tried as a sufficient condition and flagged large numbers of
        // ordinary beats whose measurement window was contaminated by a
        // neighbouring complex.
        boolean different = corr < 0.80;

        // Shape difference is DIRECTION-AGNOSTIC. Measured in the 5-15 Hz
        // detection band a ventricular complex comes out NARROWER than a sinus
        // one, not wider: the bandpass strips the low-frequency content that
        // makes a PVC broad, collapsing it into one smooth concentrated hump,
        // while a sinus QRS keeps its sharp biphasic R-S spread. On MIT-BIH
        // record 233 annotated PVCs measured widthRatio 0.39-0.74 against a
        // normal of 1.0. An earlier version tested only for widening and so had
        // the inequality backwards.
        boolean reshaped = !(widthRatio >= 0.85 && widthRatio <= 1.12);
        boolean requantified = !(areaRatio >= 0.83 && areaRatio <= 1.20);
        boolean slow = zcrRatio < 0.80 || zcrRatio > 1.25;
        boolean ventricular = different && (reshaped || requantified || slow);

        if (ventricular) { set(b, "Ventricular", (premature && compensatory) ? 0.9 : 0.7); return; }
        if (premature)   { set(b, "Supraventricular", compensatory ? 0.8 : 0.65); return; }
        if (rrRatio > 1.6) { set(b, "Other", 0.5); return; }   // dropped / escape beat
        set(b, "Normal", 0.9);
    }

    private void set(PanTompkins.Beat b, String label, double conf) {
        b.label = label;
        b.confidence = conf;
    }
}
