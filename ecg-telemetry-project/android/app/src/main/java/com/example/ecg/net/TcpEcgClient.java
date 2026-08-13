package com.example.ecg.net;

import java.io.InputStream;
import java.net.InetSocketAddress;
import java.net.Socket;

/**
 * Reads the ECG packet stream from a TCP socket instead of Bluetooth.
 * Pair with:  python3 ecg_transmitter.py --transport tcp --port 9000
 *
 * Target syntax: "tcp://192.168.1.100:9000"
 *
 * Deliberately free of android.* imports so it can be unit-tested with a plain
 * JDK, which is how the frame handling was verified.
 */
public class TcpEcgClient implements EcgSource {

    private final Callback cb;
    private Thread thread;
    private volatile boolean running = false;
    private Socket socket;

    public TcpEcgClient(Callback cb) { this.cb = cb; }

    @Override public boolean isRunning() { return running; }

    @Override public void connect(final String target) {
        stop();
        running = true;
        thread = new Thread(() -> run(target), "ecg-tcp");
        thread.start();
    }

    @Override public void stop() {
        running = false;
        try { if (socket != null) socket.close(); } catch (Exception ignored) { }
        socket = null;
        if (thread != null) { thread.interrupt(); thread = null; }
    }

    private void run(String target) {
        try {
            String t = target.trim();
            if (t.startsWith("tcp://")) t = t.substring(6);
            int colon = t.lastIndexOf(':');
            if (colon < 0) { cb.onError("Use tcp://host:port"); return; }
            String host = t.substring(0, colon);
            int port = Integer.parseInt(t.substring(colon + 1).trim());

            socket = new Socket();
            socket.connect(new InetSocketAddress(host, port), 5000);
            socket.setTcpNoDelay(true);      // 100 ms packets must not be Nagled
            cb.onConnected(host + ":" + port);

            InputStream in = socket.getInputStream();
            FrameParser parser = new FrameParser();
            byte[] chunk = new byte[1024];
            while (running) {
                int n = in.read(chunk);
                if (n < 0) break;
                if (n > 0) parser.feed(chunk, n, cb::onSamples);
            }
        } catch (NumberFormatException e) {
            cb.onError("Bad port number in \"" + target + "\"");
        } catch (Exception e) {
            if (running) cb.onError("TCP: " + e.getMessage());
        } finally {
            try { if (socket != null) socket.close(); } catch (Exception ignored) { }
            running = false;
            cb.onDisconnected();
        }
    }
}
