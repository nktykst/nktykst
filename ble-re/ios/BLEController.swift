// CoreBluetooth で解析済みプロトコルを叩く最小構成。
// Info.plist に NSBluetoothAlwaysUsageDescription を追加すること。
// バックグラウンドで使うなら UIBackgroundModes に "bluetooth-central" も追加する。
//
// 使い方 (SwiftUI):
//   @StateObject var ble = BLEController()
//   Button("ON")  { ble.send(Protocol.power(on: true)) }
//   Button("OFF") { ble.send(Protocol.power(on: false)) }

import CoreBluetooth
import Foundation

/// 解析結果をここに書く。UUID・オペコード・チェックサムは対象機器に合わせて差し替える。
enum Protocol {
    // ---- 解析で判明した UUID ----
    static let service   = CBUUID(string: "0000fff0-0000-1000-8000-00805f9b34fb")
    static let writeChar = CBUUID(string: "0000fff2-0000-1000-8000-00805f9b34fb")   // app -> device
    static let notifyChar = CBUUID(string: "0000fff1-0000-1000-8000-00805f9b34fb")  // device -> app
    /// ログで Write Command (0x52) だったなら .withoutResponse、Write Request (0x12) なら .withResponse
    static let writeType: CBCharacteristicWriteType = .withoutResponse

    // ---- パケット組み立て (例: 7e 00 <op> <arg> 00 <xor> ef) ----
    static func frame(op: UInt8, arg: UInt8) -> Data {
        var body: [UInt8] = [0x7e, 0x00, op, arg, 0x00]
        body.append(body.reduce(0, ^))   // xor8 チェックサム
        body.append(0xef)
        return Data(body)
    }
    static func power(on: Bool) -> Data { frame(op: 0x04, arg: on ? 1 : 0) }
    static func brightness(_ pct: UInt8) -> Data { frame(op: 0x01, arg: min(pct, 100)) }

    /// 接続直後に必ず送る認証/ハンドシェイクがあればここに並べる (なければ空)。
    static let handshake: [Data] = []
}

final class BLEController: NSObject, ObservableObject {
    @Published private(set) var state: String = "idle"
    @Published private(set) var lastNotify: Data = Data()

    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var writeChar: CBCharacteristic?
    private var queue: [Data] = []           // 準備完了前に send() された分を溜める
    private let savedIdKey = "ble.peripheral.identifier"

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: nil)
    }

    // MARK: public API

    func send(_ data: Data) {
        guard let p = peripheral, let c = writeChar, p.state == .connected else {
            queue.append(data)
            return
        }
        p.writeValue(data, for: c, type: Protocol.writeType)
    }

    func disconnect() {
        if let p = peripheral { central.cancelPeripheralConnection(p) }
    }

    // MARK: connect flow

    private func startScanOrReconnect() {
        // 前回接続した機器は identifier から直接取り出せる (iOS には MAC が無い)
        if let s = UserDefaults.standard.string(forKey: savedIdKey), let id = UUID(uuidString: s),
           let p = central.retrievePeripherals(withIdentifiers: [id]).first {
            connect(p)
            return
        }
        state = "scanning"
        // Service UUID で絞ると背景でも検出できる。Advertise していない機器は nil で探す (前景のみ)。
        central.scanForPeripherals(withServices: [Protocol.service], options: nil)
    }

    private func connect(_ p: CBPeripheral) {
        central.stopScan()
        peripheral = p
        p.delegate = self
        state = "connecting \(p.name ?? p.identifier.uuidString)"
        central.connect(p, options: nil)
    }

    private func ready() {
        state = "ready"
        Protocol.handshake.forEach { send($0) }
        let pending = queue; queue.removeAll()
        pending.forEach { send($0) }
    }
}

// MARK: - CBCentralManagerDelegate

extension BLEController: CBCentralManagerDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn: startScanOrReconnect()
        default: state = "bluetooth \(central.state.rawValue)"
        }
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        // 必要なら advertisementData[CBAdvertisementDataManufacturerDataKey] や名前でさらに絞る
        UserDefaults.standard.set(peripheral.identifier.uuidString, forKey: savedIdKey)
        connect(peripheral)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        state = "discovering"
        peripheral.discoverServices([Protocol.service])
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        state = "connect failed: \(error?.localizedDescription ?? "-")"
        startScanOrReconnect()
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        state = "disconnected"
        writeChar = nil
        // 自動再接続したい場合。connect() は機器が見える範囲に戻るまで待ち続ける。
        central.connect(peripheral, options: nil)
    }
}

// MARK: - CBPeripheralDelegate

extension BLEController: CBPeripheralDelegate {
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard let svc = peripheral.services?.first(where: { $0.uuid == Protocol.service }) else {
            state = "service not found"; return
        }
        peripheral.discoverCharacteristics([Protocol.writeChar, Protocol.notifyChar], for: svc)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        for c in service.characteristics ?? [] {
            if c.uuid == Protocol.writeChar { writeChar = c }
            if c.uuid == Protocol.notifyChar { peripheral.setNotifyValue(true, for: c) }  // CCCD 書き込み
        }
        // Notify 購読の完了を待ってから ready() にする (didUpdateNotificationStateFor)。
        if writeChar != nil, service.characteristics?.contains(where: { $0.uuid == Protocol.notifyChar }) != true {
            ready()
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        if characteristic.uuid == Protocol.notifyChar, characteristic.isNotifying, writeChar != nil { ready() }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard let v = characteristic.value else { return }
        lastNotify = v
        print("NOTIFY \(characteristic.uuid): \(v.map { String(format: "%02x", $0) }.joined(separator: " "))")
        // ここで解析済みの応答をデコードして @Published に反映する
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        if let e = error { print("write error: \(e)") }
    }
}
