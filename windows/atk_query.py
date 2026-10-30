r"""Ask the BIOS, through ASUS's own driver, which ATK devices exist and their state.

    py atk_query.py                 every device id zenduo_re knows
    py atk_query.py 0x00120075      just these
    py atk_query.py --range 0x00120000 0x00120100
                                    sweep a range (finds ids nobody named)

Read only: it calls DSTS (get status) and nothing else, through \\.\ATKACPI,
the device the ASUS software uses (zenduo_re.ATK_DEVICE says where that is
from). Run it as Administrator, before and after changing a MyASUS setting,
and compare: the id whose value moved is the one that setting drives. On
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


def open_atk():
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.HANDLE]
    kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    handle = kernel32.CreateFileW(zenduo_re.ATK_DEVICE, GENERIC_READ | GENERIC_WRITE,
                                  3, None, OPEN_EXISTING, 0, None)
    if not handle or handle == INVALID_HANDLE:
        sys.exit(f"atk_query: cannot open {zenduo_re.ATK_DEVICE} "
                 f"(error {ctypes.get_last_error()}). Administrator? ASUS System"
                 " Control Interface installed?")
    return kernel32, handle


def call(kernel32, handle, method, args):
    inbuf = zenduo_re.atk_call_buffer(method, args)
    outbuf = ctypes.create_string_buffer(16)
    got = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(handle, zenduo_re.ATK_IOCTL_CALL, inbuf, len(inbuf),
                                  outbuf, 16, ctypes.byref(got), None)
    return ok, outbuf.raw[:max(got.value, 4)]


def main(argv=None):
    zenduo_re.require_windows("atk_query")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("ids", nargs="*", help="device ids (hex)")
    parser.add_argument("--range", nargs=2, metavar=("FIRST", "LAST"))
    parser.add_argument("--init", action="store_true", help="send INIT first")
    parser.add_argument("--json", help="also write the results to this file")
    args = parser.parse_args(argv)

    if args.range:
        ids = range(int(args.range[0], 16), int(args.range[1], 16) + 1)
    elif args.ids:
        ids = [int(i, 16) for i in args.ids]
    else:
        ids = sorted(zenduo_re.ATK_DEVICE_IDS)
    kernel32, handle = open_atk()
    dsts = next(k for k, v in zenduo_re.ATK_METHODS.items() if v == "DSTS")
    if args.init:
        init = next(k for k, v in zenduo_re.ATK_METHODS.items() if v == "INIT")
        print("INIT ->", call(kernel32, handle, init, bytes(8))[1].hex())
    results = []
    for device in ids:
        ok, out = call(kernel32, handle, dsts, device.to_bytes(4, "little"))
        decoded = zenduo_re.decode_atk_call(
            zenduo_re.atk_call_buffer(dsts, device.to_bytes(4, "little")), out)
        decoded["ioctl_ok"] = bool(ok)
        if args.range and not decoded.get("present"):
            continue
        results.append(decoded)
        print(f"{decoded['device_id']}  {decoded['returned'] if ok else 'failed':>10}"
              f"  {'present' if decoded.get('present') else '-':8} {decoded['device']}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"written {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
