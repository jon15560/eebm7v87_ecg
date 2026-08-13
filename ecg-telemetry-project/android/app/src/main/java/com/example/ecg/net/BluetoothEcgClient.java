package com.example.ecg.net;

import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothSocket;
import android.util.Log;

import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.Method;
import java.util.Set;
import java.util.UUID;

/**
 * Part 2 - capturing the ECG on the phone over Bluetooth.
 *
 * Connects to an already-paired device exposing the Serial Port Profile and
 * pumps received bytes through FrameParser. Everything happens on its own
 * thread; the callback fires on that thread, so anything touching the UI must
 * hop to the main looper.
 *
 * Pair the transmitter in Android Settings first. Discovery from inside an app
 * needs location permission and a scan dialog, which adds nothing to this
 * project - the device is sitting on your desk.
 */
public class BluetoothEcgClient implements EcgSource {

    private static final String TAG = "BluetoothEcgClient";
    /** Well-known SPP UUID - matches the service advertised by the Python side. */
    private static final UUID SPP = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB");

    private final EcgSource.Callback cb;
    private Thread thread;
    private volatile boolean running = false;
    private BluetoothSocket socket;

    public BluetoothEcgClient(EcgSource.Callback cb) { this.cb = cb; }

    /** @param namePrefix e.g. "raspberrypi" - first paired device that matches. */
    @Override public void connect(final String namePrefix) {
        stop();
        running = true;
        thread = new Thread(() -> run(namePrefix), "ecg-bt");
        thread.start();
    }

    @Override public void stop() {
        running = false;
        try { if (socket != null) socket.close(); } catch (IOException ignored) { }
        socket = null;
        if (thread != null) { thread.interrupt(); thread = null; }
    }

    @Override public boolean isRunning() { return running; }

    private void run(String namePrefix) {
        try {
            BluetoothAdapter adapter = BluetoothAdapter.getDefaultAdapter();
            if (adapter == null) { fail("This device has no Bluetooth adapter."); return; }
            if (!adapter.isEnabled()) { fail("Bluetooth is switched off."); return; }

            BluetoothDevice target = null;
            Set<BluetoothDevice> bonded = adapter.getBondedDevices();  // needs BLUETOOTH_CONNECT
            for (BluetoothDevice d : bonded) {
                String nm = d.getName();
                if (nm != null && (namePrefix == null || namePrefix.isEmpty()
                        || nm.toLowerCase().startsWith(namePrefix.toLowerCase()))) {
                    target = d;
                    break;
                }
            }
            if (target == null) {
                fail("No paired device matching \"" + namePrefix + "\". Pair it in Settings first.");
                return;
            }

            adapter.cancelDiscovery();     // discovery cripples throughput
            socket = target.createRfcommSocketToServiceRecord(SPP);
            try {
                socket.connect();
            } catch (IOException first) {
                // Some stacks fail the SDP lookup but connect fine on channel 1.
                // This reflection fallback is well known and worth keeping.
                Log.w(TAG, "SDP connect failed, trying channel 1", first);
                try {
                    Method m = target.getClass().getMethod("createRfcommSocket", int.class);
                    socket = (BluetoothSocket) m.invoke(target, 1);
                    socket.connect();
                } catch (Exception second) {
                    fail("Could not connect: " + first.getMessage());
                    return;
                }
            }

            cb.onConnected(target.getName());

            InputStream in = socket.getInputStream();
            FrameParser parser = new FrameParser();
            byte[] chunk = new byte[1024];
            while (running) {
                int n = in.read(chunk);
                if (n < 0) break;
                if (n > 0) parser.feed(chunk, n, cb::onSamples);
            }
        } catch (IOException e) {
            if (running) fail("Link lost: " + e.getMessage());
        } catch (SecurityException e) {
            fail("Bluetooth permission was denied.");
        } finally {
            try { if (socket != null) socket.close(); } catch (IOException ignored) { }
            running = false;
            cb.onDisconnected();
        }
    }

    private void fail(String msg) {
        Log.e(TAG, msg);
        cb.onError(msg);
    }
}
