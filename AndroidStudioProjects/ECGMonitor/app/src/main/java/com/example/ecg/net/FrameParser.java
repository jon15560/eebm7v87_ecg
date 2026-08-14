package com.example.ecg.net;

/**
 * Decoder for the wire format produced by transmitter/ecg_transmitter.py:
 *
 *   0xA5 0x5A | seq:u16 LE | count:u8 | count * sample:i16 LE (uV) | xor:u8
 *
 * Bluetooth SPP is a raw byte stream with no message boundaries. Bytes arrive
 * in arbitrarily sized dribbles, the phone can connect halfway through a
 * packet, and a dropped byte would otherwise desynchronise the stream forever.
 * So the parser hunts for the sync word, validates a checksum, and on any
 * failure advances a single byte and tries again - it loses one packet, not
 * the connection.
 *
 * Feed it whatever read() returned; it keeps the partial tail internally.
 */
public class FrameParser {

    public interface Listener {
        /** @param samples millivolts, already scaled from the uV on the wire
         *  @param dropped number of packets lost since the previous callback */
        void onSamples(float[] samples, int seq, int dropped);
    }

    private static final int SYNC0 = 0xA5, SYNC1 = 0x5A;
    private static final int MAX_SAMPLES = 64;
    private static final int HEADER = 5;    // sync(2) + seq(2) + count(1)

    private byte[] buf = new byte[4096];
    private int len = 0;
    private int expectedSeq = -1;

    public void reset() { len = 0; expectedSeq = -1; }

    public void feed(byte[] data, int n, Listener l) {
        if (len + n > buf.length) {
            int cap = Math.max(buf.length * 2, len + n);
            byte[] nb = new byte[cap];
            System.arraycopy(buf, 0, nb, 0, len);
            buf = nb;
        }
        System.arraycopy(data, 0, buf, len, n);
        len += n;

        int i = 0;
        while (true) {
            while (i + 1 < len && !((buf[i] & 0xFF) == SYNC0 && (buf[i + 1] & 0xFF) == SYNC1)) i++;
            if (i + HEADER > len) break;

            int count = buf[i + 4] & 0xFF;
            // Validate the length byte BEFORE trusting it: a corrupted count
            // claiming "512 samples follow" would make us wait forever for
            // bytes that never arrive, stalling the stream permanently instead
            // of costing us one packet.
            if (count == 0 || count > MAX_SAMPLES) { i++; continue; }

            int total = HEADER + 2 * count + 1;
            if (i + total > len) break;            // genuinely incomplete: wait

            int chk = 0;
            for (int k = i + 2; k < i + total - 1; k++) chk ^= (buf[k] & 0xFF);
            if (chk != (buf[i + total - 1] & 0xFF)) { i++; continue; }

            int seq = (buf[i + 2] & 0xFF) | ((buf[i + 3] & 0xFF) << 8);
            float[] out = new float[count];
            for (int k = 0; k < count; k++) {
                int lo = buf[i + HEADER + 2 * k] & 0xFF;
                int hi = buf[i + HEADER + 2 * k + 1];      // signed: keeps the sign bit
                out[k] = (short) (lo | (hi << 8)) / 1000.0f;   // uV -> mV
            }

            int dropped = 0;
            if (expectedSeq >= 0 && seq != expectedSeq) {
                dropped = ((seq - expectedSeq) & 0xFFFF);
                if (dropped > 1000) dropped = 0;           // wrapped or restarted
            }
            expectedSeq = (seq + 1) & 0xFFFF;

            l.onSamples(out, seq, dropped);
            i += total;
        }

        // keep the unconsumed tail
        if (i > 0) {
            System.arraycopy(buf, i, buf, 0, len - i);
            len -= i;
        }
    }
}
