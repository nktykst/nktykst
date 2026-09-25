"""UUID / Company ID の名前解決。bleak の内蔵テーブルに独自の追加分を重ねる。"""

from __future__ import annotations

from bleak.uuids import normalize_uuid_str, uuidstr_to_str

# bleak に無い、または解析でよく出会うベンダー UUID
EXTRA_UUIDS: dict[str, str] = {
    "6e400001-b5a3-f393-e0a9-e50e24dcca9e": "Nordic UART Service",
    "6e400002-b5a3-f393-e0a9-e50e24dcca9e": "Nordic UART RX (write)",
    "6e400003-b5a3-f393-e0a9-e50e24dcca9e": "Nordic UART TX (notify)",
    "0000fe59-0000-1000-8000-00805f9b34fb": "Nordic Secure DFU",
    "8ec90001-f315-4f60-9fb8-838830daea50": "Nordic DFU Control Point",
    "8ec90002-f315-4f60-9fb8-838830daea50": "Nordic DFU Packet",
    "0000ffe0-0000-1000-8000-00805f9b34fb": "HM-10 UART Service (common clone)",
    "0000ffe1-0000-1000-8000-00805f9b34fb": "HM-10 UART Characteristic",
    "0000fff0-0000-1000-8000-00805f9b34fb": "Vendor service FFF0 (TI/ISSC style)",
    "0000fff1-0000-1000-8000-00805f9b34fb": "Vendor char FFF1",
    "0000fff2-0000-1000-8000-00805f9b34fb": "Vendor char FFF2",
    "0000fee7-0000-1000-8000-00805f9b34fb": "Tencent WeChat / AirSync",
    "0000fe95-0000-1000-8000-00805f9b34fb": "Xiaomi Mi Service",
    "0000feaa-0000-1000-8000-00805f9b34fb": "Eddystone",
    "0000fd6f-0000-1000-8000-00805f9b34fb": "Exposure Notification",
    "00001530-1212-efde-1523-785feabcd123": "Nordic Legacy DFU",
    "f000ffc0-0451-4000-b000-000000000000": "TI OAD Service",
}

COMPANY_IDS: dict[int, str] = {
    0x0000: "Ericsson",
    0x0001: "Nokia",
    0x0002: "Intel",
    0x0006: "Microsoft",
    0x000D: "Texas Instruments",
    0x000F: "Broadcom",
    0x0010: "STMicroelectronics",
    0x004C: "Apple",
    0x0059: "Nordic Semiconductor",
    0x0075: "Samsung",
    0x0087: "Garmin",
    0x00E0: "Google",
    0x0157: "Anhui Huami (Xiaomi band)",
    0x01DA: "Logitech",
    0x0310: "SGL Italia (Realtek)",
    0x038F: "Xiaomi",
    0x0499: "Ruuvi",
    0x0822: "adafruit",
    0x02E5: "Espressif",
    0x0171: "Amazon",
    0x0131: "Cypress",
    0x02FF: "Silicon Labs",
    0x0D00: "Realtek",
}


def uuid_name(uuid: str) -> str:
    """UUID を人が読める名前にする。未知なら 'Vendor specific'。"""
    u = normalize_uuid_str(str(uuid)).lower()
    if u in EXTRA_UUIDS:
        return EXTRA_UUIDS[u]
    return uuidstr_to_str(u)


def short_uuid(uuid: str) -> str:
    """Bluetooth SIG 基底 UUID なら 16bit 表記 (0x2a19) に短縮する。"""
    u = normalize_uuid_str(str(uuid)).lower()
    if u.endswith("-0000-1000-8000-00805f9b34fb") and u.startswith("0000"):
        return "0x" + u[4:8]
    return u


def is_vendor(uuid: str) -> bool:
    """Bluetooth SIG が割り当てた (名前の付く) UUID でなければ True。"""
    return uuid_name(uuid) in ("Vendor specific", "Unknown")


def company_name(cid: int) -> str:
    return COMPANY_IDS.get(cid, f"unknown(0x{cid:04x})")
