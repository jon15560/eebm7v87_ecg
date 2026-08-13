package com.example.ecg.net;

/**
 * A source of ECG samples. Two implementations speak the identical wire format:
 *
 *   BluetoothEcgClient - RFCOMM / SPP, the real assignment.
 *   TcpEcgClient       - the same bytes over a TCP socket.
 *
 * The TCP path exists because a Python RFCOMM *server* is painful on Windows
 * and macOS, and there is no reason to be blocked on that while building the
 * rest of the system. Since both transports carry the same packets through the
 * same FrameParser, everything downstream is unchanged, and swapping back to
 * Bluetooth later is a one-line change in MainActivity.
 */
public interface EcgSource {

    interface Callback {
        void onConnected(String name);
        /** @param dropped packets lost since the previous callback */
        void onSamples(float[] samples, int seq, int dropped);
        void onError(String message);
        void onDisconnected();
    }

    /** @param target Bluetooth device-name prefix, or "tcp://host:port". */
    void connect(String target);

    void stop();

    boolean isRunning();
}
