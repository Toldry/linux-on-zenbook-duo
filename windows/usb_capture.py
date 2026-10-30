#!/usr/bin/env python3
r"""Record the USB traffic to the docked keyboard, cut by action.

    py usb_capture.py --list-interfaces
    py usb_capture.py --scenario keyboard
    py usb_capture.py --scenario attach --interface \\.\USBPcap2

Needs USBPcap (https://desowin.org/usbpcap/, also installed by the Wireshark
installer when ticked) and an Administrator terminal. Without --interface it
records every USBPcap root hub at once, one file each, because which root hub
the pogo connector hangs off is not known yet (VERIFY-ON-HW, then pass it).

What it shows that the hooks cannot: what reaches the wire, including what
Windows' own HID stack sends on attach (before any ASUS process acts), and the
keyboard's answers: a STALL on a SET_REPORT is visible here and nowhere else.
It only covers the docked keyboard (the pogo link is USB); Bluetooth needs
hook_asus.py or Microsoft's BTVS sniffer (docs/WINDOWS-RE.md).

The USBPcapCMD options used are -d, -o, -A and --inject-descriptors (from its
--help, USBPcap 1.5.4); --usbpcap-arg passes anything else through. The
.pcap files open in Wireshark and are decoded by analyze.py on any OS.
"""

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zenduo_re  # noqa: E402

DEFAULT_EXE = r"C:\Program Files\USBPcap\USBPcapCMD.exe"


def parse_extcap_interfaces(text):
    """Interface names from `USBPcapCMD --extcap-interfaces` output."""
    return re.findall(r"interface \{value=([^}]+)\}", text)


def find_exe(explicit):
    for candidate in (explicit, DEFAULT_EXE,
                      r"C:\Program Files\Wireshark\extcap\USBPcapCMD.exe"):
        if candidate and os.path.isfile(candidate):
            return candidate
    sys.exit("usb_capture: USBPcapCMD.exe not found. Install USBPcap or pass --exe.")


def list_interfaces(exe):
    result = subprocess.run([exe, "--extcap-interfaces"], capture_output=True, text=True,
                            timeout=30)
    return parse_extcap_interfaces(result.stdout)


def main(argv=None):
    zenduo_re.require_windows("usb_capture")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--exe", help="path to USBPcapCMD.exe")
    parser.add_argument("--interface", action="append",
                        help=r"e.g. \\.\USBPcap1; repeatable; default all")
    parser.add_argument("--list-interfaces", action="store_true")
    parser.add_argument("--scenario", choices=sorted(zenduo_re.SCENARIOS))
    parser.add_argument("--usbpcap-arg", action="append", default=[],
                        help="extra argument for USBPcapCMD (repeatable)")
    parser.add_argument("--out", help="parent folder for the session (default windows/captures)")
    args = parser.parse_args(argv)

    exe = find_exe(args.exe)
    interfaces = args.interface or list_interfaces(exe)
    if args.list_interfaces or not interfaces:
        print("\n".join(interfaces) or "No USBPcap interfaces. Reboot after installing USBPcap.")
        return 0 if interfaces else 1

    out = zenduo_re.session_dir("usb", args.out)
    markers = zenduo_re.JsonLog(os.path.join(out, "markers.jsonl"))
    processes = []
    for interface in interfaces:
        name = interface.strip("\\.").replace("\\", "_")
        path = os.path.join(out, f"{name}.pcap")
        command = [exe, "-d", interface, "-o", path, "-A", "--inject-descriptors",
                   *args.usbpcap_arg]
        markers.write({"type": "capture", "interface": interface, "file": path,
                       "command": command})
        processes.append(subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE))
        print(f"capturing {interface} -> {path}")
    try:
        zenduo_re.run_markers(markers, args.scenario)
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                _, err = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                err = b""
            if process.returncode not in (0, 1, None) and err:
                print(err.decode(errors="replace").strip())
    print(f"written {out}\nnext: python analyze.py {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
