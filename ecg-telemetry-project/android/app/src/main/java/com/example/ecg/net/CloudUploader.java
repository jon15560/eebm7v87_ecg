package com.example.ecg.net;

import android.os.Build;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * Part 4 - shipping the signal from the phone to the server over Wi-Fi / mobile
 * data, and Part 5 - receiving the classification back.
 *
 * Samples arrive at 250 Hz in 25-sample blocks. Firing an HTTP request per
 * block would mean 10 requests a second, each with TCP and TLS overhead far
 * larger than its payload, and would flatten the battery. So blocks are
 * accumulated into BATCH_SAMPLES (2 s) and posted once.
 *
 * The queue is bounded and DROPS THE OLDEST batch when the network cannot keep
 * up. For a live monitor, stale ECG is worthless - falling behind forever is a
 * worse failure than losing two seconds, and an unbounded queue would grow
 * until the process was killed.
 */
public class CloudUploader {

    private static final String TAG = "CloudUploader";
    private static final int BATCH_SAMPLES = 500;      // 2 s at 250 Hz
    private static final int QUEUE_DEPTH = 8;          // ~16 s of backlog

    public interface Callback {
        void onResult(String label, int heartRate, JSONArray beats, long roundTripMs);
        void onError(String message);
    }

    private final String endpoint;
    private final String deviceId;
    private final Callback cb;

    private final BlockingQueue<float[]> queue = new ArrayBlockingQueue<>(QUEUE_DEPTH);
    private final List<Float> pending = new ArrayList<>(BATCH_SAMPLES);
    private Thread worker;
    private volatile boolean running = false;
    private int seq = 0;
    private int droppedBatches = 0;

    public CloudUploader(String endpoint, Callback cb) {
        this.endpoint = endpoint;
        this.cb = cb;
        this.deviceId = (Build.MODEL == null ? "android" : Build.MODEL.replace(' ', '-'));
    }

    public void start() {
        if (running) return;
        running = true;
        worker = new Thread(this::loop, "ecg-upload");
        worker.start();
    }

    public void stop() {
        running = false;
        if (worker != null) worker.interrupt();
        worker = null;
    }

    public int droppedBatches() { return droppedBatches; }

    /** Called from the Bluetooth thread. Never blocks. */
    public synchronized void submit(float[] samples, int n) {
        for (int i = 0; i < n; i++) {
            pending.add(samples[i]);
            if (pending.size() >= BATCH_SAMPLES) {
                float[] batch = new float[pending.size()];
                for (int k = 0; k < batch.length; k++) batch[k] = pending.get(k);
                pending.clear();
                if (!queue.offer(batch)) {
                    queue.poll();               // drop the oldest, keep the newest
                    queue.offer(batch);
                    droppedBatches++;
                }
            }
        }
    }

    private void loop() {
        while (running) {
            try {
                float[] batch = queue.poll(1, TimeUnit.SECONDS);
                if (batch != null) post(batch);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            } catch (Exception e) {
                Log.w(TAG, "upload failed", e);
                cb.onError(e.getClass().getSimpleName() + ": " + e.getMessage());
                try { Thread.sleep(1000); } catch (InterruptedException ie) { return; }
            }
        }
    }

    private void post(float[] batch) throws Exception {
        JSONArray arr = new JSONArray();
        for (float v : batch) arr.put(Math.round(v * 10000.0) / 10000.0);

        JSONObject body = new JSONObject();
        body.put("device", deviceId);
        body.put("fs", 250);
        body.put("seq", seq++);
        body.put("samples", arr);

        long t0 = System.currentTimeMillis();
        HttpURLConnection c = (HttpURLConnection) new URL(endpoint).openConnection();
        try {
            c.setRequestMethod("POST");
            c.setRequestProperty("Content-Type", "application/json");
            c.setConnectTimeout(5000);
            c.setReadTimeout(8000);
            c.setDoOutput(true);
            try (OutputStream os = c.getOutputStream()) {
                os.write(body.toString().getBytes("UTF-8"));
            }
            int code = c.getResponseCode();
            if (code != 200) {
                cb.onError("Server returned HTTP " + code);
                return;
            }
            java.io.InputStream is = c.getInputStream();
            java.io.ByteArrayOutputStream bo = new java.io.ByteArrayOutputStream();
            byte[] b = new byte[4096];
            int n;
            while ((n = is.read(b)) > 0) bo.write(b, 0, n);

            JSONObject r = new JSONObject(bo.toString("UTF-8"));
            JSONArray beats = r.optJSONArray("beats");
            int hr = r.optInt("hr", 0);
            String worst = "Normal";
            if (beats != null) {
                for (int i = 0; i < beats.length(); i++) {
                    String l = beats.getJSONObject(i).optString("label", "Normal");
                    if ("Ventricular".equals(l)) worst = l;
                    else if ("Supraventricular".equals(l) && !"Ventricular".equals(worst)) worst = l;
                }
            }
            cb.onResult(worst, hr, beats, System.currentTimeMillis() - t0);
        } finally {
            c.disconnect();
        }
    }
}
