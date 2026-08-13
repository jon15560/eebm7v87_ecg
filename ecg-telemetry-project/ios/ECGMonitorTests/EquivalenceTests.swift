import XCTest
@testable import ECGMonitor

/// Equivalence test between the Swift port and the Python reference.
///
/// The Android build verifies this by compiling the Java DSP with `javac` and
/// diffing it against Python directly (`tools/cross_check.py`). That is not
/// possible for Swift outside Xcode, so instead the Python implementation's
/// output is frozen into `reference_vectors.json` and asserted here.
///
/// Regenerate the vectors after any change to the DSP:
///     python3 tools/gen_reference_vectors.py
///
/// If this test fails, the phone and the server no longer agree, and on-device
/// results can no longer be trusted. Fix it before shipping anything.
final class EquivalenceTests: XCTestCase {

    struct Reference: Decodable {
        struct Beat: Decodable {
            let index: Int
            let rr_prev: Double
            let rr_next: Double
            let width: Double
            let amplitude: Double
            let corr: Double
            let label: String
        }
        let fs: Double
        let tolerance: Double
        let samples: [Double]
        let beats: [Beat]
    }

    private func loadReference() throws -> Reference {
        let bundle = Bundle(for: type(of: self))
        guard let url = bundle.url(forResource: "reference_vectors", withExtension: "json") else {
            XCTFail("""
                reference_vectors.json not in the test bundle.
                Add it to the test target's "Copy Bundle Resources" build phase.
                """)
            throw NSError(domain: "test", code: 1)
        }
        return try JSONDecoder().decode(Reference.self, from: Data(contentsOf: url))
    }

    /// Runs the full chain and compares every beat, feature and label.
    func testMatchesPythonReference() throws {
        let ref = try loadReference()
        let detector = PanTompkins(sampleRate: ref.fs)
        let classifier = BeatClassifier()          // rules, as used for the vectors

        var got = [PanTompkins.Beat]()
        for s in ref.samples {
            if var b = detector.process(s) {
                classifier.classify(&b)
                got.append(b)
            }
        }
        if var b = detector.flush() {
            classifier.classify(&b)
            got.append(b)
        }

        XCTAssertEqual(got.count, ref.beats.count,
                       "beat count differs: Swift found \(got.count), Python \(ref.beats.count)")

        let tol = max(ref.tolerance, 1e-6)
        for (i, (a, e)) in zip(got, ref.beats).enumerated() {
            XCTAssertEqual(a.index, e.index, "beat \(i): R-peak index")
            XCTAssertEqual(a.rrPrev, e.rr_prev, accuracy: tol, "beat \(i): rrPrev")
            XCTAssertEqual(a.rrNext, e.rr_next, accuracy: tol, "beat \(i): rrNext")
            XCTAssertEqual(a.width, e.width, accuracy: tol, "beat \(i): width")
            XCTAssertEqual(a.amplitude, e.amplitude, accuracy: tol, "beat \(i): amplitude")
            XCTAssertEqual(a.corr, e.corr, accuracy: tol, "beat \(i): corr")
            XCTAssertEqual(a.label, e.label, "beat \(i): label")
        }
    }

    /// The parser must reassemble packets split across BLE notifications, at any
    /// MTU down to the 23-byte minimum, and must not stall on a corrupted
    /// length byte.
    func testFrameParserSurvivesFragmentationAndCorruption() {
        func packet(seq: Int, samples: [Int16]) -> [UInt8] {
            var body: [UInt8] = [UInt8(seq & 0xFF), UInt8((seq >> 8) & 0xFF),
                                 UInt8(samples.count)]
            for s in samples {
                let u = UInt16(bitPattern: s)
                body.append(UInt8(u & 0xFF))
                body.append(UInt8(u >> 8))
            }
            var chk: UInt8 = 0
            for b in body { chk ^= b }
            return [0xA5, 0x5A] + body + [chk]
        }

        let payload: [Int16] = (0..<25).map { Int16($0 * 37 - 400) }
        var stream = [UInt8]()
        for s in 0..<5 { stream += packet(seq: s, samples: payload) }

        for chunk in [1, 7, 20, 56, 512] {
            let parser = FrameParser()
            var seqs = [Int]()
            var values = [[Float]]()
            var i = 0
            while i < stream.count {
                let n = min(chunk, stream.count - i)
                parser.feed(Data(stream[i..<(i + n)])) { s, seq, _ in
                    seqs.append(seq); values.append(s)
                }
                i += n
            }
            XCTAssertEqual(seqs, [0, 1, 2, 3, 4], "chunk size \(chunk): packets lost")
            XCTAssertEqual(values.first?.count, 25, "chunk size \(chunk): wrong sample count")
        }

        // Corrupt the length byte of packet 1. A parser that trusts it would
        // block forever waiting for bytes that never arrive.
        var corrupted = stream
        corrupted[56 + 4] ^= 0xFF
        let parser = FrameParser()
        var seqs = [Int]()
        var i = 0
        while i < corrupted.count {
            let n = min(7, corrupted.count - i)
            parser.feed(Data(corrupted[i..<(i + n)])) { _, seq, _ in seqs.append(seq) }
            i += n
        }
        XCTAssertEqual(seqs, [0, 2, 3, 4],
                       "corrupted length byte should cost exactly one packet")
    }

    /// Sanity check on throughput: the detector must run far faster than the
    /// 250 Hz it is fed at, or the phone cannot keep up with a live link.
    func testRealTimePerformance() throws {
        let ref = try loadReference()
        let t0 = Date()
        let detector = PanTompkins(sampleRate: ref.fs)
        let classifier = BeatClassifier()
        for s in ref.samples {
            if var b = detector.process(s) { classifier.classify(&b) }
        }
        let elapsed = Date().timeIntervalSince(t0)
        let signalSeconds = Double(ref.samples.count) / ref.fs
        let factor = signalSeconds / elapsed
        print(String(format: "processed %.0f s of ECG in %.3f s (%.0fx real time)",
                     signalSeconds, elapsed, factor))
        XCTAssertGreaterThan(factor, 10, "detector is too slow for a live stream")
    }
}
