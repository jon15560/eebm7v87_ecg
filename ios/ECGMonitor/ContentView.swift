import SwiftUI

/// Ties the parts together.
///
///   BLE in  →  EcgBuffer (draw)
///           →  on-device PanTompkins + BeatClassifier   [local mode]
///           →  CloudUploader → server                   [cloud mode]
///
/// The toggle lets you run the same signal through both paths and check that the
/// phone and the server agree — the point of keeping the Swift and the Python
/// implementations identical.
struct ContentView: View {

    private static let fs = 250.0

    @StateObject private var ble = BLEEcgClient()
    @StateObject private var buffer = EcgBuffer(sampleRate: fs, seconds: 5)

    @State private var detector = PanTompkins(sampleRate: fs)
    @State private var classifier = BeatClassifier.fromBundle()
    @State private var uploader: CloudUploader?

    @AppStorage("serverURL") private var serverURL = "http://192.168.1.100:8000/api/ecg"
    @State private var classifyOnPhone = true
    @State private var connected = false

    @State private var bpm = 0
    @State private var resultText = "waiting"
    @State private var resultLabel = "Normal"
    @State private var counts: [String: Int] = [:]
    @State private var samplesReceived = 0
    @State private var droppedPackets = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("ECG MONITOR")
                .font(.caption).bold().kerning(1.6)
                .foregroundColor(.secondary)

            HStack(alignment: .lastTextBaseline) {
                Text(bpm > 0 ? "\(bpm)" : "--")
                    .font(.system(size: 46, weight: .light))
                    .foregroundColor(colorFor("Normal"))
                Text("bpm").font(.caption).foregroundColor(.secondary)
                Spacer()
                Text(resultText).font(.subheadline).foregroundColor(colorFor(resultLabel))
            }

            EcgWaveformView(buffer: buffer).frame(maxHeight: .infinity)

            Text(statsLine).font(.caption2.monospaced()).foregroundColor(.secondary)
            Text(statusLine).font(.caption).foregroundColor(statusColor)

            TextField("http://<server-ip>:8000/api/ecg", text: $serverURL)
                .textFieldStyle(.roundedBorder)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .keyboardType(.URL)

            HStack {
                Toggle("Classify on phone", isOn: $classifyOnPhone)
                    .disabled(connected)
                Button(connected ? "Disconnect" : "Connect") {
                    connected ? disconnect() : connect()
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding()
        .onAppear(perform: wireCallbacks)
    }

    // MARK: - plumbing

    private func wireCallbacks() {
        ble.onSamples = { samples, _, dropped in
            // Bluetooth queue. Buffer + DSP here; only small updates hop to main.
            droppedPackets += dropped
            samplesReceived += samples.count
            buffer.push(samples)

            if classifyOnPhone {
                for (i, v) in samples.enumerated() {
                    if var beat = detector.process(Double(v)) {
                        classifier.classify(&beat)
                        let ago = samples.count - i
                        buffer.addBeat(samplesAgo: ago, label: beat.label)
                        let label = beat.label, conf = beat.confidence
                        let b = Int(beat.bpm.rounded())
                        DispatchQueue.main.async {
                            counts[label, default: 0] += 1
                            if b > 0 { bpm = b }
                            resultLabel = label
                            resultText = String(format: "%@  (%.0f%%)  on-device",
                                                label, conf * 100)
                        }
                    }
                }
            } else {
                uploader?.submit(samples)
            }
        }
    }

    private func connect() {
        detector = PanTompkins(sampleRate: Self.fs)
        classifier = BeatClassifier.fromBundle()
        counts = [:]; samplesReceived = 0; droppedPackets = 0
        buffer.clear()

        if !classifyOnPhone {
            let up = CloudUploader(endpoint: serverURL)
            up?.onResult = { r in
                DispatchQueue.main.async {
                    if r.heartRate > 0 { bpm = r.heartRate }
                    resultLabel = r.label
                    resultText = "\(r.label)  server \(r.roundTripMs) ms"
                    for b in r.beats {
                        counts[(b["label"] as? String) ?? "Normal", default: 0] += 1
                    }
                }
            }
            up?.onError = { msg in
                DispatchQueue.main.async { resultText = "server: \(msg)" }
            }
            up?.start()
            uploader = up
        }

        ble.start()
        connected = true
    }

    private func disconnect() {
        ble.stop()
        uploader?.stop()
        uploader = nil
        connected = false
    }

    // MARK: - display helpers

    private var statsLine: String {
        var s = String(format: "%.1f s", Double(samplesReceived) / Self.fs)
        for k in ["Normal", "Supraventricular", "Ventricular", "Other"] {
            if let v = counts[k] { s += "   \(k.prefix(1)):\(v)" }
        }
        if droppedPackets > 0 { s += "   lost \(droppedPackets)" }
        if let d = uploader?.droppedBatches, d > 0 { s += "   skipped \(d)" }
        return s
    }

    private var statusLine: String {
        switch ble.state {
        case .idle:              return "not connected"
        case .poweredOff:        return "Bluetooth is switched off"
        case .unauthorized:      return "Bluetooth permission denied — enable it in Settings"
        case .scanning:          return "scanning for ECG transmitter…"
        case .connecting:        return "connecting…"
        case .streaming(let n):  return "streaming from \(n)"
        case .failed(let m):     return m
        }
    }

    private var statusColor: Color {
        switch ble.state {
        case .streaming:                     return colorFor("Normal")
        case .scanning, .connecting:         return colorFor("Supraventricular")
        case .failed, .poweredOff, .unauthorized: return colorFor("Ventricular")
        default:                             return .secondary
        }
    }
}
