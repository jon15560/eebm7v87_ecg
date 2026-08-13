import Foundation

/// Beat classifier — Swift port of `RuleClassifier` / `LinearClassifier` from
/// `server/ecg_algorithms.py`.
///
/// Two modes:
///  • rules   — the textbook criteria; works with no training data at all.
///  • trained — multinomial logistic regression whose weights were fitted on
///    MIT-BIH by `server/train_classifier.py` and bundled as `model.json`.
///    Exactly the same weights the server uses.
///
/// Features are RATIOS against the patient's own running normal beat rather than
/// absolute millivolts and milliseconds, so one set of thresholds works across
/// leads, electrode placements and patients — absolute limits do not.
final class BeatClassifier {

    static let labels = ["Normal", "Supraventricular", "Ventricular", "Other"]

    private var mean: [Double] = []
    private var scale: [Double] = []
    private var coef: [[Double]] = []
    private var intercept: [Double] = []
    private(set) var isTrained = false

    init() {}

    /// Load weights exported by `train_classifier.py`.
    /// Returns nil if the file is missing or malformed, so the caller can fall
    /// back to the rules rather than shipping a half-initialised model.
    init?(modelJSON data: Data) {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let m = obj["mean"] as? [Double],
              let s = obj["scale"] as? [Double],
              let c = obj["coef"] as? [[Double]],
              let b = obj["intercept"] as? [Double]
        else { return nil }
        mean = m
        scale = s
        coef = c
        intercept = b
        isTrained = true
    }

    /// Convenience loader for a bundled `model.json`.
    static func fromBundle() -> BeatClassifier {
        if let url = Bundle.main.url(forResource: "model", withExtension: "json"),
           let data = try? Data(contentsOf: url),
           let clf = BeatClassifier(modelJSON: data) {
            return clf
        }
        return BeatClassifier()
    }

    /// Feature order must match `FEATURE_NAMES` in `ecg_algorithms.py`.
    static func features(_ b: PanTompkins.Beat) -> [Double] {
        let m = b.rrMean > 0 ? b.rrMean : 0.8
        let wm = b.widthMean != 0 ? b.widthMean : 1e-6
        let am = b.ampMean != 0 ? b.ampMean : 1e-6
        return [
            b.rrPrev,
            b.rrNext,
            b.rrPrev / m,
            b.rrNext / m,
            b.width / wm,
            b.amplitude / am,
            b.corr,
            (b.rrNext - b.rrPrev) / m,
            b.width,
        ]
    }

    /// Sets `label` and `confidence` in place.
    func classify(_ b: inout PanTompkins.Beat) {
        let x = BeatClassifier.features(b)
        if isTrained { predictLinear(&b, x) } else { predictRules(&b, x) }
    }

    private func predictLinear(_ b: inout PanTompkins.Beat, _ x: [Double]) {
        var z = [Double](repeating: 0, count: x.count)
        for i in 0..<x.count {
            let s = (i < scale.count && scale[i] != 0) ? scale[i] : 1.0
            z[i] = (x[i] - (i < mean.count ? mean[i] : 0)) / s
        }
        var scores = [Double](repeating: 0, count: coef.count)
        for k in 0..<coef.count {
            var s = intercept[k]
            for i in 0..<min(z.count, coef[k].count) { s += coef[k][i] * z[i] }
            scores[k] = s
        }
        let mx = scores.max() ?? 0
        var tot = 0.0
        for k in 0..<scores.count {
            scores[k] = exp(scores[k] - mx)
            tot += scores[k]
        }
        var best = 0
        for k in 1..<scores.count where scores[k] > scores[best] { best = k }
        b.label = BeatClassifier.labels[min(best, BeatClassifier.labels.count - 1)]
        b.confidence = scores[best] / tot
    }

    private func predictRules(_ b: inout PanTompkins.Beat, _ x: [Double]) {
        let rrRatio = x[2]
        let rrNextRatio = x[3]
        let widthRatio = x[4]
        let ampRatio = x[5]
        let corr = x[6]

        if ampRatio < 0.25 || widthRatio > 3.5 {
            b.label = "Other"; b.confidence = 0.6; return      // noise / lead-off
        }

        let premature = rrRatio < 0.85
        let compensatory = rrNextRatio > 1.10

        // The bedside discriminator between a PVC and an atrial premature beat is
        // the pause that follows. A PVC does not reset the sinus node, so the
        // next sinus beat arrives on schedule and the pause is fully
        // compensatory; an atrial ectopic resets it and the pause is shorter.
        let fullPause = rrNextRatio > 1.20

        // Ventricular origin means an abnormal activation path, showing up as a
        // complex that is both unlike the patient's normal AND broader. Demanding
        // both keeps ordinary beats next to an ectopic — whose correlation window
        // is contaminated by the neighbour — out of the class.
        let ventricular = (corr < 0.80 && (widthRatio > 1.05 || fullPause))
                          || widthRatio > 1.30

        if ventricular {
            b.label = "Ventricular"
            b.confidence = (premature && compensatory) ? 0.9 : 0.7
        } else if premature {
            b.label = "Supraventricular"
            b.confidence = compensatory ? 0.8 : 0.65
        } else if rrRatio > 1.6 {
            b.label = "Other"
            b.confidence = 0.5                                  // dropped / escape
        } else {
            b.label = "Normal"
            b.confidence = 0.9
        }
    }
}
