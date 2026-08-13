import SwiftUI

/// Part 3 — drawing the ECG on the phone screen.
///
/// Sweep display, like a bedside monitor: samples are written into a ring buffer
/// and a short gap is cleared just ahead of the write cursor. The alternative —
/// shifting every sample left each frame — costs an O(n) copy per frame and
/// makes the trace shimmer.
///
/// Scaling follows clinical convention where the screen allows: 25 mm/s and
/// 10 mm/mV, so the trace reads like ECG paper and QRS widths can be eyeballed
/// against the small squares.
final class EcgBuffer: ObservableObject {
    let sampleRate: Double
    private(set) var buf: [Float]
    private(set) var writeIdx = 0
    private(set) var wrapped = false

    struct Mark { let idx: Int; let label: String }
    private(set) var marks = [Mark]()

    /// Bumped to trigger a redraw. The buffer itself is mutated from the
    /// Bluetooth queue at 250 Hz; republishing on every sample would flood the
    /// main thread, so the view drives redraws from a TimelineView instead and
    /// this is only used for coarse invalidation.
    @Published var revision = 0

    private let lock = NSLock()

    init(sampleRate: Double = 250, seconds: Double = 5) {
        self.sampleRate = sampleRate
        buf = [Float](repeating: 0, count: Int(sampleRate * seconds))
    }

    func push(_ samples: [Float]) {
        lock.lock()
        for v in samples {
            buf[writeIdx] = v
            writeIdx += 1
            if writeIdx >= buf.count { writeIdx = 0; wrapped = true }
        }
        lock.unlock()
    }

    /// Annotate a beat. `samplesAgo` counts back from the current cursor.
    func addBeat(samplesAgo: Int, label: String) {
        lock.lock()
        let idx = ((writeIdx - samplesAgo) % buf.count + buf.count) % buf.count
        marks.append(Mark(idx: idx, label: label))
        if marks.count > 24 { marks.removeFirst() }
        lock.unlock()
    }

    func clear() {
        lock.lock()
        for i in 0..<buf.count { buf[i] = 0 }
        writeIdx = 0; wrapped = false; marks.removeAll()
        lock.unlock()
    }

    func snapshot() -> ([Float], Int, Bool, [Mark]) {
        lock.lock()
        defer { lock.unlock() }
        return (buf, writeIdx, wrapped, marks)
    }
}

func colorFor(_ label: String) -> Color {
    switch label {
    case "Ventricular":      return Color(red: 1.00, green: 0.42, blue: 0.42)
    case "Supraventricular": return Color(red: 1.00, green: 0.71, blue: 0.33)
    case "Other":            return Color(red: 0.49, green: 0.58, blue: 0.66)
    default:                 return Color(red: 0.22, green: 0.85, blue: 0.54)
    }
}

struct EcgWaveformView: View {
    @ObservedObject var buffer: EcgBuffer

    var body: some View {
        // ~30 fps. The screen cannot show more, and repainting on every 100 ms
        // packet would be both jerkier and more expensive.
        TimelineView(.animation(minimumInterval: 1.0 / 30.0)) { _ in
            Canvas { ctx, size in draw(ctx, size) }
        }
        .background(Color(red: 0.04, green: 0.06, blue: 0.08))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func draw(_ ctx: GraphicsContext, _ size: CGSize) {
        let (data, cursor, wrapped, marks) = buffer.snapshot()
        let n = data.count
        guard n > 1, size.width > 0, size.height > 0 else { return }
        let dx = size.width / CGFloat(n)

        drawGrid(ctx, size)

        // Autoscale to what is on screen, clamped so a flat lead is not
        // amplified into a wall of noise.
        let count = wrapped ? n : cursor
        var lo = Float.greatestFiniteMagnitude, hi = -Float.greatestFiniteMagnitude
        if count > 0 {
            for i in 0..<count { lo = min(lo, data[i]); hi = max(hi, data[i]) }
        }
        if count == 0 || hi - lo < 0.2 { lo = -1; hi = 1 }
        let mid = (hi + lo) / 2
        let gain = CGFloat(size.height * 0.8) / CGFloat(max(hi - lo, 0.2))
        let baseline = size.height / 2

        let gap = Int(0.04 * buffer.sampleRate)
        var path = Path()
        var started = false
        for i in 1..<n {
            let dist = ((i - cursor) % n + n) % n
            if i == cursor || dist < gap { started = false; continue }
            if !wrapped && i > cursor { break }
            let x = CGFloat(i) * dx
            let y = baseline - CGFloat(data[i] - mid) * gain
            if started { path.addLine(to: CGPoint(x: x, y: y)) }
            else { path.move(to: CGPoint(x: x, y: y)); started = true }
        }
        ctx.stroke(path, with: .color(colorFor("Normal")), lineWidth: 1.8)

        for m in marks {
            let x = CGFloat(m.idx) * dx
            var tick = Path()
            tick.move(to: CGPoint(x: x, y: 0))
            tick.addLine(to: CGPoint(x: x, y: size.height * 0.08))
            ctx.stroke(tick, with: .color(colorFor(m.label)), lineWidth: 2)
            if m.label != "Normal", let first = m.label.first {
                ctx.draw(Text(String(first)).font(.caption2).foregroundColor(colorFor(m.label)),
                         at: CGPoint(x: x + 6, y: size.height * 0.08 + 8))
            }
        }
    }

    private func drawGrid(_ ctx: GraphicsContext, _ size: CGSize) {
        let secondsOnScreen = CGFloat(buffer.buf.count) / CGFloat(buffer.sampleRate)
        let pxPerSec = size.width / secondsOnScreen
        var small = pxPerSec / 25.0                    // 1 mm at 25 mm/s
        if small < 3 { small = pxPerSec / 5.0 }        // too dense on a phone
        let faint = Color(red: 0.16, green: 0.08, blue: 0.09)
        let bold  = Color(red: 0.25, green: 0.13, blue: 0.16)

        var k = 0
        var x: CGFloat = 0
        while x < size.width {
            var p = Path(); p.move(to: CGPoint(x: x, y: 0)); p.addLine(to: CGPoint(x: x, y: size.height))
            ctx.stroke(p, with: .color(k % 5 == 0 ? bold : faint), lineWidth: k % 5 == 0 ? 1.0 : 0.6)
            x += small; k += 1
        }
        k = 0
        var y = size.height / 2
        while y < size.height {
            var p = Path(); p.move(to: CGPoint(x: 0, y: y)); p.addLine(to: CGPoint(x: size.width, y: y))
            ctx.stroke(p, with: .color(k % 5 == 0 ? bold : faint), lineWidth: k % 5 == 0 ? 1.0 : 0.6)
            y += small; k += 1
        }
        k = 0
        y = size.height / 2
        while y > 0 {
            var p = Path(); p.move(to: CGPoint(x: 0, y: y)); p.addLine(to: CGPoint(x: size.width, y: y))
            ctx.stroke(p, with: .color(k % 5 == 0 ? bold : faint), lineWidth: k % 5 == 0 ? 1.0 : 0.6)
            y -= small; k += 1
        }
    }
}
