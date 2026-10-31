r"""Capture everything for one scenario, in one terminal.

    py capture.py --scenario keyboard-docked
    py capture.py --scenario keyboard-bluetooth
    py capture.py --scenario power
    py capture.py --list-scenarios

Run from an Administrator terminal. This starts, together:

  * the Frida hooks in the ASUS processes (hook_asus.HookSession), which see
    the ATK calls and the HID reports MyASUS sends, over USB or Bluetooth;
  * a listener on the keyboard's vendor HID collections
    (hid_probe.HidListener), which sees what the keyboard sends back;
  * a USB capture of every root hub (usb_capture.UsbCapture), when USBPcap is
    installed, for what crosses the pogo pins (skipped over Bluetooth);
  * the WMI activity trace (wmi_trace.WmiTrace), for BIOS calls that go
    through the WMI service instead of the hooked processes;
  * a read of the BIOS settings after every step (atk_query.AtkReader, DSTS
    only), which names the setting a step changed and its old and new value
    even when the call that changed it was not seen.

Then it walks the scenario one step at a time. The screen shows only the
steps and, after each, what was caught during it; every event goes to
events.log in the session folder instead. Keyboard steps wait until Windows
sees the keyboard docked or on Bluetooth, so they cannot be done in the wrong
state by mistake. At the end everything is stopped and timeline.txt (the
analyze.py output) is written next to the raw files.

Any part that cannot start is reported before step 1 and the rest still run;
--no-hooks, --no-hid, --no-usb, --no-wmi and --no-atk leave a part out.
"""

import argparse
import contextlib
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zenduo_re  # noqa: E402


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scenario", choices=sorted(zenduo_re.SCENARIOS),
                        help="what to walk through; without it, type your own labels")
    parser.add_argument("--list-scenarios", action="store_true")
    parser.add_argument("--no-hooks", action="store_true", help="leave out the Frida hooks")
    parser.add_argument("--no-hid", action="store_true", help="leave out the HID listener")
    parser.add_argument("--no-usb", action="store_true", help="leave out the USB capture")
    parser.add_argument("--no-wmi", action="store_true", help="leave out the WMI trace")
    parser.add_argument("--no-atk", action="store_true",
                        help="leave out the BIOS read after each step")
    parser.add_argument("--all-ioctls", action="store_true",
                        help="hooks log every DeviceIoControl (noisy)")
    parser.add_argument("--interface", action="append",
                        help=r"USBPcap root hub, e.g. \\.\USBPcap1; default all")
    parser.add_argument("--out", help="parent folder for the session (default windows/captures)")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        for name, plan in zenduo_re.SCENARIOS.items():
            print(f"{name:<20} {len(plan['steps']):>3} steps  {plan['title']}")
        return 0
    zenduo_re.require_windows("capture")
    import atk_query
    import hid_probe
    import hook_asus
    import usb_capture
    import wmi_trace

    paint = zenduo_re.Paint(sys.stdout)
    if not is_admin():
        sys.exit(paint("warn", "capture: run this from an Administrator terminal")
                 + " (Start, type Terminal, right-click it, Run as administrator).")
    plan = zenduo_re.SCENARIOS.get(args.scenario)
    over_bluetooth = bool(plan) and plan["keyboard"] == "bluetooth"

    out = zenduo_re.session_dir(f"capture-{args.scenario or 'free'}", args.out)
    markers = zenduo_re.JsonLog(os.path.join(out, "markers.jsonl"))
    events = zenduo_re.EventLog(os.path.join(out, "events.log"))
    report = []           # (ok, text) per part, shown before step 1
    parts = {}

    def ok(text):
        report.append((True, text))

    def bad(text):
        report.append((False, text))

    if not args.no_hooks:
        try:
            frida = hook_asus.import_frida()
        except SystemExit as error:
            bad(f"hooks: {error}")
        else:
            log = zenduo_re.JsonLog(os.path.join(out, "hooks.jsonl"))
            parts["hooks"] = hook_asus.HookSession(frida, log, events,
                                                   all_ioctls=args.all_ioctls)
            parts["hooks"].start()
    if not args.no_hid:
        log = zenduo_re.JsonLog(os.path.join(out, "hid-input.jsonl"))
        parts["hid"] = hid_probe.HidListener(log, events)
        parts["hid"].start()
    if args.no_usb:
        pass
    elif over_bluetooth:
        ok("USB capture: left out, the keyboard is on Bluetooth in this run")
    else:
        exe = usb_capture.find_exe()
        interfaces = (args.interface or usb_capture.list_interfaces(exe)) if exe else []
        if not exe:
            bad(f"USB capture: {usb_capture.NOT_FOUND}")
        elif not interfaces:
            bad("USB capture: no USBPcap root hubs. Reboot after installing USBPcap.")
        else:
            parts["usb"] = usb_capture.UsbCapture(exe, interfaces, out, markers)
            parts["usb"].start()
            ok(f"USB capture: {len(interfaces)} root hub(s)")
    if not args.no_wmi:
        trace = wmi_trace.WmiTrace()
        error = trace.start()
        if error:
            bad(f"WMI trace: {error}")
        else:
            parts["wmi"] = trace
            ok("WMI trace: on (read back at the end)")

    if not args.no_atk:
        try:
            parts["atk"] = atk_query.AtkReader()
        except OSError as error:
            bad(f"BIOS read: {error}")
        else:
            ok(f"BIOS read: {len(zenduo_re.ATK_DEVICE_IDS)} settings read after every"
               " step, plus any the hooks see")

    time.sleep(2.0)       # first hook and HID scans
    if "hooks" in parts:
        hooked = parts["hooks"].hooked()
        if any("myasus" in name.lower() for name in hooked):
            ok(f"hooks: {len(hooked)} ASUS process(es), MyASUS among them")
        else:
            bad(f"hooks: {len(hooked)} ASUS process(es), MyASUS not running yet."
                " Open it now; it is hooked within 5 s of starting.")
        for name in parts["hooks"].failed():
            bad(f"hooks: could not hook {name} (protected process?)")
    if "hid" in parts:
        count = len(parts["hid"].listening())
        (ok if count else bad)(f"HID: listening on {count} keyboard vendor collection(s)"
                               + ("" if count else ", more are picked up when it connects"))

    def detect():
        try:
            return zenduo_re.keyboard_transport(hid_probe.present_paths())
        except OSError:
            return None

    print(paint("step", f" Capture: {args.scenario or 'free labels'} "))
    for good, text in report:
        print("  " + (paint("ok", "✓ ") if good else paint("warn", "✗ ")) + text)
    state = detect()
    print("  " + paint("info", f"keyboard now: {state or 'unknown'}"))
    print("  " + paint("info", f"output: {out}"))
    print("  " + paint("info", "every captured event goes to events.log there,"
                       " not to this screen"))

    usb_before = [0]
    atk_log = zenduo_re.JsonLog(os.path.join(out, "atk-changes.jsonl"))
    atk_now = {}

    def watched_ids():
        found = parts["hooks"].atk_ids if "hooks" in parts else set()
        return sorted(set(zenduo_re.ATK_DEVICE_IDS) | found)

    if "atk" in parts:
        atk_now.update(parts["atk"].values(watched_ids()))

    def after_step(index):
        summary = zenduo_re.count_summary(events.take_counts())
        if "usb" in parts:
            now = parts["usb"].bytes_written()
            grown, usb_before[0] = now - usb_before[0], now
            summary += f"; USB +{grown / 1000:.1f} kB"
        if "atk" in parts:
            # Read the BIOS again and name what this step changed: the same
            # before/after comparison as atk_query.py, once per step.
            after = parts["atk"].values(watched_ids())
            changes = zenduo_re.atk_changes(atk_now, after)
            atk_now.update(after)
            for change in changes:
                linux = zenduo_re.linux_equivalent_atk(
                    {"method": "DSTS", "device_id": change["device_id"]})
                atk_log.write({"type": "atk-change", "step": index, "linux": linux,
                               **change})
                events.add(None, "BIOS changed: " + zenduo_re.describe_atk_change(change))
            if changes:
                summary += "\n  " + paint("do", "BIOS changed: " + "; ".join(
                    zenduo_re.describe_atk_change(c) for c in changes))
        return "caught: " + summary

    try:
        zenduo_re.run_steps(markers, args.scenario, detect=detect, after_step=after_step)
    except KeyboardInterrupt:
        markers.write({"type": "marker", "label": "capture stopped"})
    finally:
        print(paint("info", "stopping the capture ..."))
        for name in ("hooks", "hid"):
            if name in parts:
                parts[name].stop()
        for error in parts["usb"].stop() if "usb" in parts else []:
            print(paint("warn", f"USB capture: {error}"))
        wmi_count = len(parts["wmi"].stop(out)) if "wmi" in parts else 0
        if "atk" in parts:
            parts["atk"].close()
        atk_log.close()
        events.close()
        markers.close()

    import analyze
    timeline_path = os.path.join(out, "timeline.txt")
    with open(timeline_path, "w", encoding="utf-8") as f, contextlib.redirect_stdout(f):
        analyze.main([out, "--quiet-opens"])
    totals = zenduo_re.count_summary(events.total)
    print(paint("ok", f"Done. Caught {totals}, {wmi_count} WMI operation(s)."))
    print(f"  per step: {timeline_path}")
    print(f"  folder to copy to Linux: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
