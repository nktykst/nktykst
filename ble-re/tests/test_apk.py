import zipfile

from ble_re.apk import format_report, scan


def test_scan_apk_like_zip(tmp_path):
    apk = tmp_path / "app.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("classes.dex", b"\x00junk 6E400001-B5A3-F393-E0A9-E50E24DCCA9E junk writeCharacteristic 0000180a-0000-1000-8000-00805f9b34fb")
        z.writestr("lib/arm64/libfoo.so", "12345678-1234-5678-9abc-def012345678".encode("utf-16-le"))
        z.writestr("res/x.png", b"\x89PNG")
    counts, where, api_files = scan(apk)
    assert counts["6e400001-b5a3-f393-e0a9-e50e24dcca9e"] == 1
    assert counts["12345678-1234-5678-9abc-def012345678"] == 1
    assert "classes.dex" in api_files and "classes.dex" in where["0000180a-0000-1000-8000-00805f9b34fb"]
    rep = format_report(counts, where, api_files)
    assert "ベンダー UUID" in rep and "Device Information" in rep
