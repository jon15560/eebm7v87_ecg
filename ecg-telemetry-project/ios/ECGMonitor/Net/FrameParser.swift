import Foundation

/// Decoder for the wire format produced by `transmitter/ble_transmitter.py`:
///
///     0xA5 0x5A | seq:u16 LE | count:u8 | count × sample:i16 LE (µV) | xor:u8
///
/// A BLE notification carries at most ATT_MTU−3 bytes, so a packet may arrive
/// split across several notifications; the phone may also subscribe partway
/// through one. This is therefore a *stream* parser, not a packet parser: it
/// hunts for the sync word, validates a checksum, and on any failure advances a
/// single byte and retries — losing one packet rather than the connection.
///
/// Identical logic to the Android `FrameParser.java`, so both platforms decode
/// byte-for-byte the same.
final class FrameParser {

    /// - Parameter dropped: packets lost since the previous callback.
    typealias Listener = (_ samples: [Float], _ seq: Int, _ dropped: Int) -> Void

    private static let sync0: UInt8 = 0xA5
    private static let sync1: UInt8 = 0x5A
    private static let maxSamples = 64
    private static let header = 5        // sync(2) + seq(2) + count(1)

    private var buf = [UInt8]()
    private var expectedSeq = -1

    func reset() {
        buf.removeAll(keepingCapacity: true)
        expectedSeq = -1
    }

    func feed(_ data: Data, _ listener: Listener) {
        buf.append(contentsOf: data)

        var i = 0
        while true {
            while i + 1 < buf.count &&
                  !(buf[i] == FrameParser.sync0 && buf[i + 1] == FrameParser.sync1) {
                i += 1
            }
            if i + FrameParser.header > buf.count { break }

            let count = Int(buf[i + 4])
            // Validate the length byte BEFORE trusting it: a corrupted count
            // claiming "512 samples follow" would make us wait forever for bytes
            // that never arrive, stalling the stream permanently instead of
            // costing us one packet.
            if count == 0 || count > FrameParser.maxSamples { i += 1; continue }

            let total = FrameParser.header + 2 * count + 1
            if i + total > buf.count { break }          // genuinely incomplete

            var chk: UInt8 = 0
            for k in (i + 2)..<(i + total - 1) { chk ^= buf[k] }
            if chk != buf[i + total - 1] { i += 1; continue }

            let seq = Int(buf[i + 2]) | (Int(buf[i + 3]) << 8)
            var out = [Float](repeating: 0, count: count)
            for k in 0..<count {
                let lo = UInt16(buf[i + FrameParser.header + 2 * k])
                let hi = UInt16(buf[i + FrameParser.header + 2 * k + 1])
                let raw = Int16(bitPattern: lo | (hi << 8))    // keeps the sign
                out[k] = Float(raw) / 1000.0                   // µV → mV
            }

            var dropped = 0
            if expectedSeq >= 0 && seq != expectedSeq {
                dropped = (seq - expectedSeq) & 0xFFFF
                if dropped > 1000 { dropped = 0 }              // wrapped/restarted
            }
            expectedSeq = (seq + 1) & 0xFFFF

            listener(out, seq, dropped)
            i += total
        }

        if i > 0 { buf.removeFirst(i) }
    }
}
