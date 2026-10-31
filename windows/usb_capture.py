r"""Record the USB traffic to the docked keyboard, cut by action.

    py usb_capture.py --list-interfaces
    py usb_capture.py --scenario keyboard-docked
    py usb_capture.py --scenario attach --interface \\.\USBPcap2

Needs USBPcap: install Wireshark (https://www.wireshark.org/download.html)
with the "USBPcap" component ticked, then reboot. Run from an Administrator
terminal. Without --interface it
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
import glob
import os
import re
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zenduo_re  # noqa: E402

DEFAULT_EXE = r"C:\Program Files\USBPcap\USBPcapCMD.exe"
NOT_FOUND = ("USBPcapCMD.exe not found. Install Wireshark with the USBPcap"
             " component ticked and reboot, or pass --exe.")


def parse_extcap_interfaces(text):
    """Interface names from `USBPcapCMD --extcap-interfaces` output."""
    return re.findall(r"interface \{value=([^}]+)\}", text)


def find_exe(explicit=None):
    """USBPcapCMD.exe, or None."""
    for candidate in (explicit, DEFAULT_EXE,
                      r"C:\Program Files\Wireshark\extcap\USBPcapCMD.exe"):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def list_interfaces(exe):
    result = subprocess.run([exe, "--extcap-interfaces"], capture_output=True, text=True,
                            timeout=30)
    return parse_extcap_interfaces(result.stdout)


class UsbCapture:
    """One USBPcapCMD per root hub, writing <hub>.pcap into `out`."""

    def __init__(self, exe, interfaces, out, log, extra_args=()):
        self.exe, self.interfaces, self.out = exe, interfaces, out
        self.log, self.extra_args = log, list(extra_args)
        self.processes = []

    def start(self):
        for interface in self.interfaces:
            name = interface.strip("\\.").replace("\\", "_")
            path = os.path.join(self.out, f"{name}.pcap")
            command = [self.exe, "-d", interface, "-o", path, "-A",
                       "--inject-descriptors", *self.extra_args]
            self.log.write({"type": "capture", "interface": interface, "file": path,
                            "command": command})
            self.processes.append(subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE))

    def bytes_written(self):
        total = 0
        for path in glob.glob(os.path.join(self.out, "*.pcap")):
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
        return total

    def stop(self):
        """Stop every capture; returns the error text of any that failed."""
        errors = []
        for process in self.processes:
            process.terminate()
        for process in self.processes:
            try:
                _, err = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                err = b""
            if process.returncode not in (0, 1, None) and err:
                errors.append(err.decode(errors="replace").strip())
        # The capture holds every key typed on the docked keyboard; blank the
        # text before anyone shares the folder (analyze.scrub_pcap).
        import analyze
        for path in glob.glob(os.path.join(self.out, "*.pcap")):
            try:
                analyze.scrub_pcap(path)
            except (OSError, struct.error, ValueError) as error:
                errors.append(f"could not blank typed keys in {path}: {error}")
        return errors


def main(argv=None):
    zenduo_re.require_windows("usb_capture")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--exe", help="path to USBPcapCMD.exe")
    parser.add_argument("--interface", action="append",
                        help=r"e.g. \\.\USBPcap1; repeatable; default all")
    parser.add_argument("--list-interfaces", action="store_true")
    parser.add_argument("--scenario", choices=sorted(zenduo_re.SCENARIOS))
    parser.add_argument("--steps", help=zenduo_re.STEPS_HELP)
    parser.add_argument("--usbpcap-arg", action="append", default=[],
                        help="extra argument for USBPcapCMD (repeatable)")
    parser.add_argument("--out", help="parent folder for the session (default windows/captures)")
    args = parser.parse_args(argv)
    only = zenduo_re.selected_steps(args)

    exe = find_exe(args.exe)
    if not exe:
        sys.exit(NOT_FOUND)
    interfaces = args.interface or list_interfaces(exe)
    if args.list_interfaces or not interfaces:
        print("\n".join(interfaces) or "No USBPcap interfaces. Reboot after installing USBPcap.")
        return 0 if interfaces else 1

    out = zenduo_re.session_dir("usb", args.out)
    markers = zenduo_re.JsonLog(os.path.join(out, "markers.jsonl"))
    capture = UsbCapture(exe, interfaces, out, markers, args.usbpcap_arg)
    capture.start()
    print(f"capturing {', '.join(interfaces)}")
    try:
        zenduo_re.run_steps(markers, args.scenario, only=only)
    finally:
        for error in capture.stop():
            print(error)
    print(f"written {out}\nnext: py analyze.py {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
