package com.example.ecg;

import android.Manifest;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.widget.Button;
import android.widget.EditText;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import com.example.ecg.dsp.BeatClassifier;
import com.example.ecg.dsp.PanTompkins;
import com.example.ecg.net.BluetoothEcgClient;
import com.example.ecg.net.EcgSource;
import com.example.ecg.net.TcpEcgClient;
import com.example.ecg.net.CloudUploader;
import com.example.ecg.ui.EcgView;

import java.io.InputStream;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Ties the five parts together.
 *
 *   Bluetooth in  ->  EcgView (draw)
 *                 ->  on-device PanTompkins + BeatClassifier   [local mode]
 *                 ->  CloudUploader -> server                  [cloud mode]
 *
 * The toggle lets you run the same signal through both paths and check that the
 * phone and the server agree, which is the point of keeping the Java and the
 * Python implementations identical.
 */
public class MainActivity extends AppCompatActivity {

    private static final float FS = 250f;
    private static final int REQ_BT = 41;

    private EcgView ecgView;
    private TextView hrText, statusText, resultText, statsText;
    private EditText deviceField, serverField;
    private Switch localSwitch;
    private Button connectButton;

    private EcgSource source;
    private CloudUploader uploader;

    private PanTompkins detector;
    private BeatClassifier classifier;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final Map<String, Integer> counts = new LinkedHashMap<>();
    private long samplesReceived = 0;
    private int droppedPackets = 0;
    private long lastRedraw = 0;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        setContentView(R.layout.activity_main);

        ecgView = findViewById(R.id.ecgView);
        hrText = findViewById(R.id.hrText);
        statusText = findViewById(R.id.statusText);
        resultText = findViewById(R.id.resultText);
        statsText = findViewById(R.id.statsText);
        deviceField = findViewById(R.id.deviceField);
        serverField = findViewById(R.id.serverField);
        localSwitch = findViewById(R.id.localSwitch);
        connectButton = findViewById(R.id.connectButton);

        ecgView.setSampleRate(FS);
        resetPipeline();

        connectButton.setOnClickListener(v -> {
            if (source != null && source.isRunning()) disconnect(); else requestPermissionsThenConnect();
        });
    }

    private void resetPipeline() {
        detector = new PanTompkins(FS);
        classifier = loadClassifier();
        counts.clear();
        samplesReceived = 0;
        droppedPackets = 0;
    }

    /** Uses the trained weights if assets/model.json is present, else the rules. */
    private BeatClassifier loadClassifier() {
        try (InputStream is = getAssets().open("model.json")) {
            byte[] b = new byte[is.available()];
            int read = is.read(b);
            if (read > 0) {
                BeatClassifier c = new BeatClassifier(new String(b, 0, read, "UTF-8"));
                if (c.isTrained()) return c;
            }
        } catch (Exception ignored) { }
        return new BeatClassifier();
    }

    // ------------------------------------------------------------------
    private void requestPermissionsThenConnect() {
        // A tcp:// target needs no Bluetooth permission at all.
        if (deviceField.getText().toString().trim().startsWith("tcp://")) { connect(); return; }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            String[] need = {Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_SCAN};
            for (String p : need) {
                if (ContextCompat.checkSelfPermission(this, p) != PackageManager.PERMISSION_GRANTED) {
                    ActivityCompat.requestPermissions(this, need, REQ_BT);
                    return;
                }
            }
        }
        connect();
    }

    @Override
    public void onRequestPermissionsResult(int code, @NonNull String[] p, @NonNull int[] r) {
        super.onRequestPermissionsResult(code, p, r);
        if (code != REQ_BT) return;
        for (int g : r) {
            if (g != PackageManager.PERMISSION_GRANTED) {
                Toast.makeText(this, "Bluetooth permission is required.", Toast.LENGTH_LONG).show();
                return;
            }
        }
        connect();
    }

    private void connect() {
        resetPipeline();
        ecgView.clear();
        setStatus("connecting...", "#FFB454");

        if (!localSwitch.isChecked()) {
            uploader = new CloudUploader(serverField.getText().toString().trim(), cloudCallback);
            uploader.start();
        }

        String target = deviceField.getText().toString().trim();
        source = target.startsWith("tcp://") ? new TcpEcgClient(sourceCallback)
                                             : new BluetoothEcgClient(sourceCallback);
        source.connect(target);
        connectButton.setText("Disconnect");
    }

    private void disconnect() {
        if (source != null) source.stop();
        if (uploader != null) { uploader.stop(); uploader = null; }
        connectButton.setText("Connect");
        setStatus("disconnected", "#7D93A8");
    }

    // ------------------------------------------------------------------
    private final EcgSource.Callback sourceCallback = new EcgSource.Callback() {
        @Override public void onConnected(String name) {
            ui.post(() -> setStatus("streaming from " + name, "#39D98A"));
        }

        @Override public void onSamples(float[] samples, int seq, int dropped) {
            // Bluetooth thread. Draw buffer + DSP here; UI work is posted.
            droppedPackets += dropped;
            samplesReceived += samples.length;
            ecgView.push(samples, samples.length);

            if (localSwitch.isChecked()) {
                for (int i = 0; i < samples.length; i++) {
                    PanTompkins.Beat b = detector.process(samples[i]);
                    if (b != null) {
                        classifier.classify(b);
                        onBeat(b, samples.length - i);
                    }
                }
            } else if (uploader != null) {
                uploader.submit(samples, samples.length);
            }

            // Repaint at ~30 fps rather than once per 100 ms block: invalidate()
            // is cheap but the full redraw is not, and the screen cannot show
            // more than its refresh rate anyway.
            long now = System.currentTimeMillis();
            if (now - lastRedraw > 33) {
                lastRedraw = now;
                ecgView.postInvalidateOnAnimation();
                ui.post(MainActivity.this::updateStats);
            }
        }

        @Override public void onError(String msg) {
            ui.post(() -> { setStatus(msg, "#FF6B6B"); connectButton.setText("Connect"); });
        }

        @Override public void onDisconnected() {
            ui.post(() -> { setStatus("disconnected", "#7D93A8"); connectButton.setText("Connect"); });
        }
    };

    /** A beat found on the phone (local mode). */
    private void onBeat(PanTompkins.Beat b, int samplesAgo) {
        counts.merge(b.label, 1, Integer::sum);
        ecgView.addBeat(samplesAgo, b.label, colorFor(b.label));
        final int bpm = (int) Math.round(b.bpm());
        final String label = b.label;
        final double conf = b.confidence;
        ui.post(() -> {
            hrText.setText(bpm > 0 ? String.valueOf(bpm) : "--");
            resultText.setText(String.format("%s  (%.0f%%)  on-device", label, conf * 100));
            resultText.setTextColor(colorFor(label));
        });
    }

    private final CloudUploader.Callback cloudCallback = new CloudUploader.Callback() {
        @Override public void onResult(String label, int hr, org.json.JSONArray beats, long ms) {
            if (beats != null) {
                for (int i = 0; i < beats.length(); i++) {
                    String l = beats.optJSONObject(i) == null ? "Normal"
                            : beats.optJSONObject(i).optString("label", "Normal");
                    counts.merge(l, 1, Integer::sum);
                }
            }
            ui.post(() -> {
                hrText.setText(hr > 0 ? String.valueOf(hr) : "--");
                resultText.setText(label + "  server " + ms + " ms");
                resultText.setTextColor(colorFor(label));
            });
        }

        @Override public void onError(String msg) {
            ui.post(() -> setStatus("server: " + msg, "#FF6B6B"));
        }
    };

    private void updateStats() {
        StringBuilder sb = new StringBuilder();
        sb.append(String.format("%.1f s", samplesReceived / FS));
        for (Map.Entry<String, Integer> e : counts.entrySet()) {
            sb.append("   ").append(e.getKey(), 0, 1).append(":").append(e.getValue());
        }
        if (droppedPackets > 0) sb.append("   lost ").append(droppedPackets);
        if (uploader != null && uploader.droppedBatches() > 0) {
            sb.append("   skipped ").append(uploader.droppedBatches());
        }
        statsText.setText(sb.toString());
    }

    private static int colorFor(String label) {
        switch (label) {
            case "Ventricular": return Color.parseColor("#FF6B6B");
            case "Supraventricular": return Color.parseColor("#FFB454");
            case "Other": return Color.parseColor("#7D93A8");
            default: return Color.parseColor("#39D98A");
        }
    }

    private void setStatus(String s, String color) {
        statusText.setText(s);
        statusText.setTextColor(Color.parseColor(color));
    }

    @Override protected void onDestroy() {
        super.onDestroy();
        disconnect();
    }
}
