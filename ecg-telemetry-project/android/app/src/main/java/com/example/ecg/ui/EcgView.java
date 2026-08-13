package com.example.ecg.ui;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.util.AttributeSet;
import android.view.View;

import java.util.ArrayDeque;

/**
 * Part 3 - drawing the ECG on the phone screen.
 *
 * Sweep display, like a bedside monitor: the trace is written into a ring
 * buffer and a gap is cleared just ahead of the write cursor. The alternative -
 * shifting every sample left each frame - costs an O(n) memmove per frame and
 * makes the trace shimmer.
 *
 * Scaling follows clinical convention where the screen allows: 25 mm/s
 * horizontally and 10 mm/mV vertically, so the trace looks like real ECG paper
 * and QRS widths can be eyeballed against the small squares (each 40 ms).
 *
 * Thread safety: push() is called from the Bluetooth reader thread and only
 * touches the ring buffer and the write index; onDraw reads them. Values are
 * floats and a torn read costs at most one visibly wrong pixel for one frame,
 * which is not worth a lock in the audio-rate path.
 */
public class EcgView extends View {

    private static final float SECONDS_ON_SCREEN = 5f;
    private static final float MM_PER_SEC = 25f;    // standard paper speed
    private static final float MM_PER_MV = 10f;     // standard gain

    private float sampleRate = 250f;
    private float[] buf = new float[(int) (SECONDS_ON_SCREEN * 250)];
    private volatile int writeIdx = 0;
    private volatile boolean wrapped = false;

    /** R-peak markers: position in the ring buffer + colour. */
    private static class Mark { int idx; int color; String text; }
    private final ArrayDeque<Mark> marks = new ArrayDeque<>();

    private final Paint grid = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint gridBold = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint trace = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint markPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint label = new Paint(Paint.ANTI_ALIAS_FLAG);

    private float[] lines = new float[0];
    private float gain = 1f;          // px per mV
    private float baseline = 0f;

    public EcgView(Context c) { super(c); init(); }
    public EcgView(Context c, AttributeSet a) { super(c, a); init(); }

    private void init() {
        grid.setColor(Color.parseColor("#2A1418"));
        grid.setStrokeWidth(1f);
        gridBold.setColor(Color.parseColor("#402028"));
        gridBold.setStrokeWidth(1.5f);
        trace.setColor(Color.parseColor("#39D98A"));
        trace.setStrokeWidth(2.2f);
        trace.setStyle(Paint.Style.STROKE);
        markPaint.setStrokeWidth(2f);
        label.setColor(Color.parseColor("#8FA6BC"));
        label.setTextSize(26f);
        setBackgroundColor(Color.parseColor("#0B0F14"));
    }

    public void setSampleRate(float fs) {
        sampleRate = fs;
        int n = (int) (SECONDS_ON_SCREEN * fs);
        if (n != buf.length) {
            buf = new float[n];
            writeIdx = 0;
            wrapped = false;
            synchronized (marks) { marks.clear(); }
        }
    }

    /** Called from the Bluetooth thread for every received block. */
    public void push(float[] samples, int n) {
        for (int i = 0; i < n; i++) {
            buf[writeIdx] = samples[i];
            writeIdx++;
            if (writeIdx >= buf.length) { writeIdx = 0; wrapped = true; }
        }
    }

    /** Annotate the most recent beat. samplesAgo counts back from the cursor. */
    public void addBeat(int samplesAgo, String labelText, int color) {
        Mark m = new Mark();
        m.idx = Math.floorMod(writeIdx - samplesAgo, buf.length);
        m.color = color;
        m.text = labelText;
        synchronized (marks) {
            marks.addLast(m);
            while (marks.size() > 24) marks.removeFirst();
        }
    }

    public void clear() {
        java.util.Arrays.fill(buf, 0f);
        writeIdx = 0;
        wrapped = false;
        synchronized (marks) { marks.clear(); }
    }

    @Override
    protected void onDraw(Canvas c) {
        super.onDraw(c);
        final int w = getWidth(), h = getHeight();
        if (w == 0 || h == 0) return;

        final int n = buf.length;
        final float dx = (float) w / n;

        drawGrid(c, w, h, dx);

        // Autoscale to the signal actually on screen, clamped so that a flat
        // lead does not get amplified into a wall of noise.
        float lo = Float.MAX_VALUE, hi = -Float.MAX_VALUE;
        int count = wrapped ? n : writeIdx;
        for (int i = 0; i < count; i++) {
            float v = buf[i];
            if (v < lo) lo = v;
            if (v > hi) hi = v;
        }
        if (count == 0 || hi - lo < 0.2f) { lo = -1f; hi = 1f; }
        baseline = h * 0.5f;
        gain = (h * 0.8f) / Math.max(hi - lo, 0.2f);
        float mid = (hi + lo) * 0.5f;

        if (lines.length < n * 4) lines = new float[n * 4];
        int p = 0;
        int cursor = writeIdx;
        int gap = (int) (0.04f * sampleRate);      // erased sweep gap
        for (int i = 1; i < n; i++) {
            // skip the segment that wraps the ring and the gap ahead of the cursor
            int distFromCursor = Math.floorMod(i - cursor, n);
            if (i == cursor || distFromCursor < gap) continue;
            if (!wrapped && i > writeIdx) break;
            lines[p++] = (i - 1) * dx;
            lines[p++] = baseline - (buf[i - 1] - mid) * gain;
            lines[p++] = i * dx;
            lines[p++] = baseline - (buf[i] - mid) * gain;
        }
        c.drawLines(lines, 0, p, trace);

        synchronized (marks) {
            for (Mark m : marks) {
                float x = m.idx * dx;
                markPaint.setColor(m.color);
                c.drawLine(x, 0, x, h * 0.08f, markPaint);
                if (!"Normal".equals(m.text)) {
                    label.setColor(m.color);
                    c.drawText(m.text.substring(0, 1), x + 4, h * 0.08f + 26, label);
                }
            }
        }
        label.setColor(Color.parseColor("#5A7186"));
        c.drawText("25 mm/s   10 mm/mV   " + (int) sampleRate + " Hz", 12, h - 14, label);
    }

    private void drawGrid(Canvas c, int w, int h, float dx) {
        // one small square = 40 ms = 1 mm at 25 mm/s
        float pxPerSec = w / SECONDS_ON_SCREEN;
        float small = pxPerSec / MM_PER_SEC;
        if (small < 3) small = pxPerSec / 5f;      // too dense on a small screen
        int k = 0;
        for (float x = 0; x < w; x += small, k++) {
            c.drawLine(x, 0, x, h, (k % 5 == 0) ? gridBold : grid);
        }
        k = 0;
        for (float y = h / 2f; y < h; y += small, k++) {
            c.drawLine(0, y, w, y, (k % 5 == 0) ? gridBold : grid);
        }
        k = 0;
        for (float y = h / 2f; y > 0; y -= small, k++) {
            c.drawLine(0, y, w, y, (k % 5 == 0) ? gridBold : grid);
        }
    }
}
