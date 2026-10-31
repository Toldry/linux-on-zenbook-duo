r"""Ask the BIOS, through ASUS's own driver, which ATK devices exist and their state.

    py atk_query.py                 every device id zenduo_re knows
    py atk_query.py 0x00120075      just these
    py atk_query.py --range 0x00120000 0x00120100
                                    sweep a range (finds ids nobody named)
    py atk_query.py --save before   keep this read under a name ...
    py atk_query.py --compare before
                                    ... and later print only what changed

Read only: it calls DSTS (get status) and nothing else, through \\.\ATKACPI,
the device the ASUS software uses (zenduo_re.ATK_DEVICE says where that is
from). Run it as Administrator, before and after changing a MyASUS setting,
and compare: the id whose value moved is the one that setting drives.
capture.py does that after every step on its own; --save/--compare are for
a change that only applies after a restart. On
Linux the same query is the asus-wmi debugfs dsts file, so the two outputs
compare line by line (`analyze.py --atk-compare`).

Why INIT is not called first: G-Helper sends INIT once at start-up, but the
ASUS services have long done that by the time this runs. If every DSTS comes
back 0xfffffffe on a machine where MyASUS works, that guess was wrong; try
--init (it writes nothing the services do not already send at boot).
"""

import argparse
import ctypes
import json
import os
import sys
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zenduo_re  # noqa: E402

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE = ctypes.c_void_p(-1).value


class AtkReader:
    r"""A read-only handle on \\.\ATKACPI that answers DSTS calls."""

    def __init__(self):
        kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                         ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.HANDLE]
        kernel32.DeviceIoControl.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.CreateFileW(zenduo_re.ATK_DEVICE, GENERIC_READ | GENERIC_WRITE,
                                      3, None, OPEN_EXISTING, 0, None)
        if not handle or handle == INVALID_HANDLE:
            raise OSError(f"cannot open {zenduo_re.ATK_DEVICE} (error"
                          f" {ctypes.get_last_error()}). Administrator? ASUS System"
                          " Control Interface installed?")
        self.kernel32, self.handle = kernel32, handle

    def call(self, method, args):
        inbuf = zenduo_re.atk_call_buffer(method, args)
        outbuf = ctypes.create_string_buffer(16)
        got = wintypes.DWORD()
        ok = self.kernel32.DeviceIoControl(self.handle, zenduo_re.ATK_IOCTL_CALL, inbuf,
                                           len(inbuf), outbuf, 16, ctypes.byref(got), None)
        return ok, outbuf.raw[:max(got.value, 4)]

    def dsts(self, device):
        """The decoded DSTS answer for one device id."""
        args = device.to_bytes(4, "little")
        ok, out = self.call(DSTS, args)
        decoded = zenduo_re.decode_atk_call(zenduo_re.atk_call_buffer(DSTS, args), out)
        decoded["ioctl_ok"] = bool(ok)
        return decoded

    def values(self, ids):
        """{device id: answer as an int, or None if the call failed}."""
        out = {}
        for device in ids:
            decoded = self.dsts(device)
            out[device] = (int(decoded["returned"], 16)
                           if decoded["ioctl_ok"] and "returned" in decoded else None)
        return out

    def close(self):
        self.kernel32.CloseHandle(self.handle)


DSTS = next(k for k, v in zenduo_re.ATK_METHODS.items() if v == "DSTS")


def saved_path(name):
    """A --save/--compare name -> windows/captures/atk-<name>.json."""
    if name.endswith(".json"):
        return name
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"atk-{name}.json")


def main(argv=None):
    zenduo_re.require_windows("atk_query")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("ids", nargs="*", help="device ids (hex)")
    parser.add_argument("--range", nargs=2, metavar=("FIRST", "LAST"))
    parser.add_argument("--init", action="store_true", help="send INIT first")
    parser.add_argument("--json", help="also write the results to this file")
    parser.add_argument("--save", metavar="NAME",
                        help="keep this read as NAME (windows/captures/atk-NAME.json)")
    parser.add_argument("--compare", metavar="NAME",
                        help="print only what changed since the read saved as NAME")
    args = parser.parse_args(argv)

    if args.range:
        ids = range(int(args.range[0], 16), int(args.range[1], 16) + 1)
    elif args.ids:
        ids = [int(i, 16) for i in args.ids]
    else:
        ids = sorted(zenduo_re.ATK_DEVICE_IDS)
    try:
        reader = AtkReader()
    except OSError as error:
        sys.exit(f"atk_query: {error}")
    if args.init:
        init = next(k for k, v in zenduo_re.ATK_METHODS.items() if v == "INIT")
        print("INIT ->", reader.call(init, bytes(8))[1].hex())
    if args.compare:
        with open(saved_path(args.compare), encoding="utf-8") as f:
            saved = json.load(f)
        before = {int(r["device_id"], 16): int(r["returned"], 16)
                  for r in saved if r.get("ioctl_ok") and "returned" in r}
        changes = [c for c in zenduo_re.atk_changes(before, reader.values(sorted(before)))
                   if int(c["device_id"], 16) not in zenduo_re.ATK_LIVE_IDS]
        print("\n".join(zenduo_re.describe_atk_change(c) for c in changes)
              or f"nothing changed since '{args.compare}'")
        return 0
    results = []
    for device in ids:
        decoded = reader.dsts(device)
        if args.range and not decoded.get("present"):
            continue
        results.append(decoded)
        print(f"{decoded['device_id']}  "
              f"{decoded.get('returned', '?') if decoded['ioctl_ok'] else 'failed':>10}"
              f"  {'present' if decoded.get('present') else '-':8} {decoded['device']}")
    for path in filter(None, (args.json, args.save and saved_path(args.save))):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"written {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
