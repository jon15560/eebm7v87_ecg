import com.example.ecg.dsp.*;
import java.io.*;
import java.nio.file.*;

/**
 * Runs the Android DSP over samples on stdin and prints one line per beat.
 * cross_check.py runs the Python implementation over the identical input and
 * diffs the two.
 *
 * Optional argument: a path to model.json. With it, the trained tree classifier
 * is exercised; without it, the rule-based fallback. Both paths are checked,
 * because the tree is what actually ships once a model has been trained.
 */
public class CrossCheck {
    public static void main(String[] a) throws Exception {
        BeatClassifier c;
        if (a.length > 0) {
            String json = new String(Files.readAllBytes(Paths.get(a[0])), "UTF-8");
            c = new BeatClassifier(json);
            if (!c.isTrained()) {
                System.err.println("WARNING: model.json failed to load, using rules");
            }
        } else {
            c = new BeatClassifier();
        }

        BufferedReader r = new BufferedReader(new InputStreamReader(System.in));
        PanTompkins d = new PanTompkins(250.0);
        StringBuilder sb = new StringBuilder();
        String line;
        while ((line = r.readLine()) != null) {
            if (line.isEmpty()) continue;
            PanTompkins.Beat b = d.process(Double.parseDouble(line));
            if (b != null) { c.classify(b); sb.append(fmt(b)); }
        }
        PanTompkins.Beat b = d.flush();
        if (b != null) { c.classify(b); sb.append(fmt(b)); }
        System.out.print(sb);
    }

    static String fmt(PanTompkins.Beat b) {
        return String.format("%d %.6f %.6f %.6f %.6f %.6f %s%n",
            b.index, b.rrPrev, b.rrNext, b.width, b.amplitude, b.corr, b.label);
    }
}
