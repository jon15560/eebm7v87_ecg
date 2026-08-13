import Foundation
import CoreBluetooth

/// Part 2 for iOS — capturing the ECG over Bluetooth Low Energy.
///
/// iOS gives third-party apps no access to Bluetooth Classic SPP/RFCOMM; that
/// profile is reserved for MFi-certified hardware. So where the Android build
/// opens an RFCOMM socket, this acts as a BLE *central*, discovers the
/// transmitter's GATT service, and subscribes to notifications.
///
/// Everything above the transport is unchanged: the bytes are identical and go
/// through the same FrameParser, which already tolerates arbitrary
/// fragmentation — exactly what BLE notification chunking produces.
final class BLEEcgClient: NSObject, ObservableObject {

    /// Nordic UART Service — the de-facto "bytes over BLE" convention, and the
    /// UUIDs advertised by ble_transmitter.py.
    static let serviceUUID = CBUUID(string: "6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
    static let txUUID      = CBUUID(string: "6E400003-B5A3-F393-E0A9-E50E24DCCA9E")

    enum State: Equatable {
        case idle, poweredOff, unauthorized, scanning, connecting
        case streaming(String), failed(String)
    }

    @Published private(set) var state: State = .idle

    /// Called on the Bluetooth queue, not the main thread.
    var onSamples: ((_ samples: [Float], _ seq: Int, _ dropped: Int) -> Void)?

    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private let parser = FrameParser()
    private let queue = DispatchQueue(label: "ecg.ble")
    private var wantScan = false

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: queue)
    }

    func start() {
        wantScan = true
        parser.reset()
        if central.state == .poweredOn { beginScan() }
    }

    func stop() {
        wantScan = false
        central.stopScan()
        if let p = peripheral { central.cancelPeripheralConnection(p) }
        peripheral = nil
        setState(.idle)
    }

    private func beginScan() {
        setState(.scanning)
        // Filtering by service UUID is required: iOS will not report peripherals
        // that are only advertising a name while the app is backgrounded, and an
        // unfiltered scan drains the battery.
        central.scanForPeripherals(withServices: [BLEEcgClient.serviceUUID],
                                   options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
    }

    private func setState(_ s: State) {
        DispatchQueue.main.async { self.state = s }
    }
}

extension BLEEcgClient: CBCentralManagerDelegate {

    func centralManagerDidUpdateState(_ c: CBCentralManager) {
        switch c.state {
        case .poweredOn:     if wantScan { beginScan() }
        case .poweredOff:    setState(.poweredOff)
        case .unauthorized:  setState(.unauthorized)
        case .unsupported:   setState(.failed("This device has no BLE support."))
        default:             break
        }
    }

    func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        c.stopScan()
        peripheral = p                       // must hold a strong reference or
        p.delegate = self                    // iOS deallocates it mid-connect
        setState(.connecting)
        c.connect(p, options: nil)
    }

    func centralManager(_ c: CBCentralManager, didConnect p: CBPeripheral) {
        parser.reset()
        p.discoverServices([BLEEcgClient.serviceUUID])
    }

    func centralManager(_ c: CBCentralManager, didFailToConnect p: CBPeripheral,
                        error: Error?) {
        setState(.failed("Connection failed: \(error?.localizedDescription ?? "unknown")"))
        if wantScan { beginScan() }
    }

    func centralManager(_ c: CBCentralManager, didDisconnectPeripheral p: CBPeripheral,
                        error: Error?) {
        setState(.idle)
        if wantScan { beginScan() }          // transmitter restarted: reconnect
    }
}

extension BLEEcgClient: CBPeripheralDelegate {

    func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        guard let svc = p.services?.first(where: { $0.uuid == BLEEcgClient.serviceUUID })
        else { setState(.failed("ECG service not found")); return }
        p.discoverCharacteristics([BLEEcgClient.txUUID], for: svc)
    }

    func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor service: CBService,
                    error: Error?) {
        guard let ch = service.characteristics?.first(where: { $0.uuid == BLEEcgClient.txUUID })
        else { setState(.failed("ECG characteristic not found")); return }
        p.setNotifyValue(true, for: ch)
        setState(.streaming(p.name ?? "ECG transmitter"))
    }

    func peripheral(_ p: CBPeripheral, didUpdateValueFor ch: CBCharacteristic,
                    error: Error?) {
        guard let data = ch.value, !data.isEmpty else { return }
        parser.feed(data) { [weak self] samples, seq, dropped in
            self?.onSamples?(samples, seq, dropped)
        }
    }
}
