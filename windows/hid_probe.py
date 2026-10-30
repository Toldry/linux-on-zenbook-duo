r"""Look at the ASUS HID devices from Windows, the way Windows sees them.

    py hid_probe.py list               every ASUS (0b05) HID collection: usage
                                       page, report lengths, declared report ids
    py hid_probe.py read-feature 5a    GET_FEATURE on that report id, on every
                                       collection that declares it
    py hid_probe.py listen [--scenario keyboard-docked]
                                       log input reports from the vendor
                                       collections (0xff31 etc.) with markers
                                       (capture.py does this and more)
    py hid_probe.py send 5a ba c5 c4 01 --yes
                                       one SET_FEATURE, to test a hypothesis

Stdlib only (ctypes on hid.dll and setupapi.dll). list, read-feature and
listen change nothing. `send` is the only write, it refuses report ids other
than 0x5a/0x5d/0x5e, and it needs --yes: it is for checking that a captured
command does what we think, with the ASUS services stopped.

Why read-feature is interesting: on Linux, GET_FEATURE 0x5a right after the
handshake returns "ASUS Tech.Inc.", and after any later 0x5a write it returns
that write instead (docs/HARDWARE.md). If the Windows keyboard behaves the
same, reading 0x5a after MyASUS did something shows the last command it sent,
with no capture at all. That the buffer behaves the same under Windows is a
guess: VERIFY.

Windows gives each open handle its own copy of the input reports, so listen
does not steal key presses from the ASUS service; but Windows opens keyboard
and mouse collections exclusively, so only the vendor collections can be read.
"""

import argparse
import ctypes
import functools
import os
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zenduo_re  # noqa: E402

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
DIGCF_PRESENT = 0x2
DIGCF_DEVICEINTERFACE = 0x10
HIDP_STATUS_SUCCESS = 0x00110000
HIDP_INPUT, HIDP_OUTPUT, HIDP_FEATURE = 0, 1, 2
CAPS_STRUCT_SIZE = 72       # HIDP_VALUE_CAPS and HIDP_BUTTON_CAPS (hidpi.h)
INVALID_HANDLE = ctypes.c_void_p(-1).value


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
                ("InputReportByteLength", wintypes.USHORT),
                ("OutputReportByteLength", wintypes.USHORT),
                ("FeatureReportByteLength", wintypes.USHORT),
                ("Reserved", wintypes.USHORT * 17),
                ("NumberLinkCollectionNodes", wintypes.USHORT),
                ("NumberInputButtonCaps", wintypes.USHORT),
                ("NumberInputValueCaps", wintypes.USHORT),
                ("NumberInputDataIndices", wintypes.USHORT),
                ("NumberOutputButtonCaps", wintypes.USHORT),
                ("NumberOutputValueCaps", wintypes.USHORT),
                ("NumberOutputDataIndices", wintypes.USHORT),
                ("NumberFeatureButtonCaps", wintypes.USHORT),
                ("NumberFeatureValueCaps", wintypes.USHORT),
                ("NumberFeatureDataIndices", wintypes.USHORT)]


@functools.lru_cache(maxsize=None)     # the scans call it every few seconds
def load_dlls():
    hid = ctypes.WinDLL("hid.dll")
    setupapi = ctypes.WinDLL("setupapi.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.HANDLE]
    kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR,
                                              wintypes.HWND, wintypes.DWORD]
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID), wintypes.DWORD,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    for name in ("HidD_GetAttributes", "HidD_GetPreparsedData", "HidD_FreePreparsedData",
                 "HidD_GetFeature", "HidD_SetFeature", "HidD_GetProductString"):
        getattr(hid, name).restype = wintypes.BOOLEAN
    hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
    hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    hid.HidD_GetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
    hid.HidD_SetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
    hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
    hid.HidP_GetCaps.restype = ctypes.c_long
    hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
    for name in ("HidP_GetValueCaps", "HidP_GetButtonCaps"):
        getattr(hid, name).restype = ctypes.c_long
        getattr(hid, name).argtypes = [ctypes.c_int, ctypes.c_void_p,
                                       ctypes.POINTER(wintypes.USHORT), ctypes.c_void_p]
    return hid, setupapi, kernel32


def interface_paths(hid, setupapi):
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    info = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    paths = []
    index = 0
    try:
        while True:
            data = SP_DEVICE_INTERFACE_DATA()
            data.cbSize = ctypes.sizeof(data)
            if not setupapi.SetupDiEnumDeviceInterfaces(info, None, ctypes.byref(guid),
                                                        index, ctypes.byref(data)):
                break
            index += 1
            needed = wintypes.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(data), None, 0,
                                                      ctypes.byref(needed), None)
            buf = ctypes.create_string_buffer(needed.value)
            # cbSize of SP_DEVICE_INTERFACE_DETAIL_DATA_W: 8 on 64-bit, 6 on 32-bit.
            ctypes.c_uint32.from_buffer(buf).value = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            if setupapi.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(data), buf,
                                                         needed, None, None):
                paths.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(info)
    return paths


def open_path(kernel32, path, write=True):
    """Read/write when Windows allows it, else a query-only handle."""
    for access in ((GENERIC_READ | GENERIC_WRITE) if write else GENERIC_READ, 0):
        handle = kernel32.CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                      None, OPEN_EXISTING, 0, None)
        if handle and handle != INVALID_HANDLE:
            return handle, access != 0
    return None, False


def report_ids(hid, preparsed, report_type, count):
    """{report id: declared bits} for one report type, from the caps arrays."""
    ids = {}
    for getter in (hid.HidP_GetValueCaps, hid.HidP_GetButtonCaps):
        n = wintypes.USHORT(max(count, 1) * 4)
        buf = ctypes.create_string_buffer(CAPS_STRUCT_SIZE * n.value)
        if getter(report_type, buf, ctypes.byref(n), preparsed) != HIDP_STATUS_SUCCESS:
            continue
        for i in range(n.value):
            raw = buf.raw[i * CAPS_STRUCT_SIZE:(i + 1) * CAPS_STRUCT_SIZE]
            rid = raw[2]
            bits = 0
            if getter is hid.HidP_GetValueCaps:
                bit_size = int.from_bytes(raw[18:20], "little")
                count_ = int.from_bytes(raw[20:22], "little")
                bits = bit_size * count_
            ids[rid] = ids.get(rid, 0) + bits
    return ids


def collections(vendor_only=True):
    hid, setupapi, kernel32 = load_dlls()
    found = []
    for path in interface_paths(hid, setupapi):
        handle, writable = open_path(kernel32, path)
        if not handle:
            continue
        try:
            attrs = HIDD_ATTRIBUTES()
            attrs.Size = ctypes.sizeof(attrs)
            if not hid.HidD_GetAttributes(handle, ctypes.byref(attrs)):
                continue
            if vendor_only and attrs.VendorID != zenduo_re.ASUS_VID:
                continue
            name = ctypes.create_unicode_buffer(128)
            hid.HidD_GetProductString(handle, name, ctypes.sizeof(name))
            entry = {"path": path, "vid": f"{attrs.VendorID:04x}",
                     "pid": f"{attrs.ProductID:04x}", "version": f"{attrs.VersionNumber:04x}",
                     "product": name.value, "writable": writable,
                     "bluetooth": zenduo_re.is_bluetooth_path(path)}
            preparsed = ctypes.c_void_p()
            if hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
                caps = HIDP_CAPS()
                if hid.HidP_GetCaps(preparsed, ctypes.byref(caps)) == HIDP_STATUS_SUCCESS:
                    entry.update({
                        "usage_page": f"0x{caps.UsagePage:04x}", "usage": f"0x{caps.Usage:04x}",
                        "input_len": caps.InputReportByteLength,
                        "output_len": caps.OutputReportByteLength,
                        "feature_len": caps.FeatureReportByteLength,
                        "feature_ids": sorted(report_ids(
                            hid, preparsed, HIDP_FEATURE,
                            caps.NumberFeatureValueCaps + caps.NumberFeatureButtonCaps)),
                        "input_ids": sorted(report_ids(
                            hid, preparsed, HIDP_INPUT,
                            caps.NumberInputValueCaps + caps.NumberInputButtonCaps)),
                    })
                hid.HidD_FreePreparsedData(preparsed)
            found.append(entry)
        finally:
            kernel32.CloseHandle(handle)
    return found


def cmd_list(args):
    entries = collections(vendor_only=not args.all)
    if not entries:
        print("No ASUS HID collections found. Is the keyboard docked or paired?")
        return 1
    for e in entries:
        print(f"{e['vid']}:{e['pid']} v{e['version']} {e['product']!r}"
              f"{' [Bluetooth]' if e['bluetooth'] else ''}")
        print(f"   usage page {e.get('usage_page')} usage {e.get('usage')}  "
              f"in/out/feature bytes {e.get('input_len')}/{e.get('output_len')}/"
              f"{e.get('feature_len')}  writable={e['writable']}")
        print(f"   feature ids {[hex(i) for i in e.get('feature_ids', [])]}  "
              f"input ids {[hex(i) for i in e.get('input_ids', [])]}")
        print(f"   {e['path']}")
    if args.json:
        import json
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
        print(f"written {args.json}")
    return 0


def cmd_read_feature(args):
    hid, _setupapi, kernel32 = load_dlls()
    report_id = int(args.report_id, 16)
    hits = 0
    for e in collections():
        if report_id not in e.get("feature_ids", []):
            continue
        handle, _ = open_path(kernel32, e["path"])
        if not handle:
            continue
        try:
            buf = ctypes.create_string_buffer(e["feature_len"])
            buf[0] = report_id
            ok = hid.HidD_GetFeature(handle, buf, e["feature_len"])
            data = buf.raw
            hits += 1
            print(f"{e['pid']} {e['usage_page']}: "
                  f"{data.hex(' ') if ok else 'GET_FEATURE failed'}")
            if ok:
                text = data[1:].split(b"\x00")[0]
                if text.isascii() and len(text) > 3:
                    print(f"   as text: {text.decode()!r}")
                print(f"   meaning: {zenduo_re.describe_feature(data)}")
        finally:
            kernel32.CloseHandle(handle)
    if not hits:
        print(f"No collection declares feature report 0x{report_id:02x}.")
    return 0 if hits else 1


def present_paths():
    """Every HID interface path Windows lists now (cheap: opens nothing)."""
    hid, setupapi, _kernel32 = load_dlls()
    return interface_paths(hid, setupapi)


def is_vendor(entry):
    return entry.get("usage_page", "0x0000") >= "0xff00"


class HidListener:
    """Reads input reports from the ASUS vendor collections until stop().

    The keyboard comes and goes (dock, undock, Bluetooth), so the collections
    are looked for again every `rescan` seconds; a reader ends when ReadFile
    fails, which is what removing the device does. Reports go to `log`
    (hid-input.jsonl) and `events`; nothing is printed.
    """

    def __init__(self, log, events, every_collection=False, rescan=3.0):
        self.log, self.events = log, events
        self.every_collection, self.rescan = every_collection, rescan
        _hid, _setupapi, self.kernel32 = load_dlls()
        self.active = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._scan_forever, daemon=True).start()

    def listening(self):
        with self._lock:
            return sorted(self.active)

    def _scan_forever(self):
        while not self._stop.is_set():
            try:
                found = collections()
            except OSError as error:
                self.events.add(None, f"HID scan failed: {error}")
                found = []
            for entry in found:
                if not (is_vendor(entry) or self.every_collection):
                    continue
                with self._lock:
                    if entry["path"] in self.active:
                        continue
                    self.active.add(entry["path"])
                threading.Thread(target=self._reader, args=(entry,), daemon=True).start()
            self._stop.wait(self.rescan)

    def _reader(self, entry):
        where = (f"{entry['pid']} {entry['usage_page']}"
                 f"{' (Bluetooth)' if entry['bluetooth'] else ''}")
        handle, _ = open_path(self.kernel32, entry["path"], write=False)
        if not handle:
            self.log.write({"type": "error", "path": entry["path"], "error": "cannot open"})
            self.events.add(None, f"cannot open {where}")
            with self._lock:
                self.active.discard(entry["path"])
            return
        self.events.add(None, f"listening on {where}")
        size = max(entry.get("input_len") or 0, 2)
        buf = ctypes.create_string_buffer(size)
        got = wintypes.DWORD()
        while not self._stop.is_set():
            if not self.kernel32.ReadFile(handle, buf, size, ctypes.byref(got), None):
                error = ctypes.get_last_error()
                self.log.write({"type": "error", "path": entry["path"],
                                "error": f"ReadFile {error}"})
                self.events.add(None, f"stopped listening on {where} (ReadFile {error},"
                                      " usually the keyboard went away)")
                break
            data = buf.raw[:got.value]
            record = {"type": "input", "pid": entry["pid"],
                      "usage_page": entry["usage_page"],
                      "bluetooth": entry["bluetooth"], "data": data.hex()}
            meaning = zenduo_re.describe_vendor_input(data)
            if meaning:
                record["meaning"] = meaning
            self.log.write(record)
            self.events.add("keyboard report",
                            f"HID input {where}: {data.hex(' ')}"
                            f"{'  ' + meaning if meaning else ''}")
        self.kernel32.CloseHandle(handle)
        with self._lock:
            self.active.discard(entry["path"])

    def stop(self):
        self._stop.set()        # readers blocked in ReadFile end with the process


def cmd_listen(args):
    out = zenduo_re.session_dir("hid-listen", args.out)
    log = zenduo_re.JsonLog(os.path.join(out, "hid-input.jsonl"))
    events = zenduo_re.EventLog(os.path.join(out, "events.log"))
    markers = zenduo_re.JsonLog(os.path.join(out, "markers.jsonl"))
    listener = HidListener(log, events, args.every_collection)
    listener.start()
    time.sleep(1.0)
    print(f"listening on {len(listener.listening())} vendor collection(s);"
          f" each report goes to {events.path}")
    zenduo_re.run_steps(markers, args.scenario,
                        detect=lambda: zenduo_re.keyboard_transport(present_paths()),
                        after_step=lambda _i: zenduo_re.count_summary(events.take_counts()))
    listener.stop()
    events.close()
    print(f"written {out}")
    return 0


def cmd_send(args):
    hid, _setupapi, kernel32 = load_dlls()
    data = bytes(int(b, 16) for b in args.bytes)
    if data[0] not in (0x5A, 0x5D, 0x5E):
        sys.exit("send: only report ids 5a, 5d and 5e are allowed.")
    if not args.yes:
        sys.exit("send: writes to the keyboard. Re-run with --yes if you mean it.")
    sent = 0
    for e in collections():
        if data[0] not in e.get("feature_ids", []):
            continue
        handle, writable = open_path(kernel32, e["path"])
        if not handle or not writable:
            print(f"{e['usage_page']}: cannot open for writing")
            continue
        try:
            buf = ctypes.create_string_buffer(data.ljust(e["feature_len"], b"\x00"),
                                              e["feature_len"])
            ok = hid.HidD_SetFeature(handle, buf, e["feature_len"])
            print(f"{e['usage_page']}: SET_FEATURE {'accepted' if ok else 'refused'}"
                  " (accepted proves little: docs/HARDWARE.md)")
            sent += bool(ok)
        finally:
            kernel32.CloseHandle(handle)
    return 0 if sent else 1


def main(argv=None):
    zenduo_re.require_windows("hid_probe")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--all", action="store_true", help="every HID device, not just ASUS")
    p.add_argument("--json", help="also write the list to this file")
    p = sub.add_parser("read-feature")
    p.add_argument("report_id", help="hex, e.g. 5a")
    p = sub.add_parser("listen")
    p.add_argument("--scenario", choices=sorted(zenduo_re.SCENARIOS))
    p.add_argument("--every-collection", action="store_true",
                   help="also try non-vendor collections (Windows usually refuses)")
    p.add_argument("--out", help="parent folder for the session (default windows/captures)")
    p = sub.add_parser("send")
    p.add_argument("bytes", nargs="+", help="hex bytes, report id first")
    p.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    return {"list": cmd_list, "read-feature": cmd_read_feature,
            "listen": cmd_listen, "send": cmd_send}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
