import Foundation

/// Part 4 — shipping the signal to the server over Wi-Fi / cellular, and Part 5
/// — receiving the classification back.
///
/// Samples arrive at 250 Hz in 25-sample blocks. One request per block would be
/// ten requests a second, each with more protocol overhead than payload, and
/// would flatten the battery. Blocks are therefore accumulated into 2-second
/// batches and posted once.
///
/// The queue is bounded and DROPS THE OLDEST batch when the network cannot keep
/// up. For a live monitor, stale ECG is worthless — falling permanently behind
/// is a worse failure than losing two seconds, and an unbounded queue would grow
/// until iOS terminated the app for memory pressure.
final class CloudUploader {

    struct Result {
        let label: String
        let heartRate: Int
        let beats: [[String: Any]]
        let roundTripMs: Int
    }

    private let endpoint: URL
    private let deviceID: String
    private let batchSamples = 500        // 2 s at 250 Hz
    private let queueDepth = 8            // ~16 s of backlog

    var onResult: ((Result) -> Void)?
    var onError: ((String) -> Void)?

    private var pending = [Float]()
    private var queue = [[Float]]()
    private let lock = NSLock()
    private var seq = 0
    private(set) var droppedBatches = 0
    private var running = false
    private let session: URLSession

    init?(endpoint: String, deviceID: String = UIDeviceName()) {
        guard let url = URL(string: endpoint) else { return nil }
        self.endpoint = url
        self.deviceID = deviceID
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 8
        cfg.waitsForConnectivity = false
        session = URLSession(configuration: cfg)
    }

    func start() { running = true; pump() }
    func stop()  { running = false }

    /// Called from the Bluetooth queue. Never blocks.
    func submit(_ samples: [Float]) {
        lock.lock()
        pending.append(contentsOf: samples)
        while pending.count >= batchSamples {
            let batch = Array(pending.prefix(batchSamples))
            pending.removeFirst(batchSamples)
            if queue.count >= queueDepth {
                queue.removeFirst()       // drop oldest, keep newest
                droppedBatches += 1
            }
            queue.append(batch)
        }
        lock.unlock()
        pump()
    }

    private var inFlight = false

    private func pump() {
        guard running else { return }
        lock.lock()
        if inFlight || queue.isEmpty { lock.unlock(); return }
        let batch = queue.removeFirst()
        inFlight = true
        let s = seq
        seq += 1
        lock.unlock()
        post(batch, seq: s)
    }

    private func post(_ batch: [Float], seq: Int) {
        let body: [String: Any] = [
            "device": deviceID,
            "fs": 250,
            "seq": seq,
            "samples": batch.map { (Double($0) * 10000).rounded() / 10000 },
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: body) else {
            finish(); return
        }

        var req = URLRequest(url: endpoint)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = data

        let t0 = Date()
        session.dataTask(with: req) { [weak self] data, response, error in
            guard let self = self else { return }
            defer { self.finish() }

            if let error = error { self.onError?(error.localizedDescription); return }
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                let code = (response as? HTTPURLResponse)?.statusCode ?? -1
                self.onError?("Server returned HTTP \(code)")
                return
            }
            guard let data = data,
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            else { self.onError?("Malformed response"); return }

            let beats = obj["beats"] as? [[String: Any]] ?? []
            let hr = obj["hr"] as? Int ?? 0
            var worst = "Normal"
            for b in beats {
                let l = b["label"] as? String ?? "Normal"
                if l == "Ventricular" { worst = l }
                else if l == "Supraventricular" && worst != "Ventricular" { worst = l }
            }
            let ms = Int(Date().timeIntervalSince(t0) * 1000)
            self.onResult?(Result(label: worst, heartRate: hr, beats: beats, roundTripMs: ms))
        }.resume()
    }

    private func finish() {
        lock.lock(); inFlight = false; lock.unlock()
        pump()
    }
}

#if canImport(UIKit)
import UIKit
func UIDeviceName() -> String {
    UIDevice.current.name.replacingOccurrences(of: " ", with: "-")
}
#else
func UIDeviceName() -> String { "ios-device" }
#endif
