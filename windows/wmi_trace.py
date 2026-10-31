r"""Record which WMI methods the ASUS software calls, cut by action.

    py wmi_trace.py --scenario power
    py wmi_trace.py --parse exported.xml      decode an export again

Some ASUS components reach the BIOS through the WMI class AsusAtkWmi_WMNB
(namespace root\wmi) instead of \\.\ATKACPI. Those calls go through the WMI
service, so hook_asus.py, which watches the ASUS processes, does not see
them. Windows can log them: the analytic channel
Microsoft-Windows-WMI-Activity/Trace records each operation with the client
process id. This tool clears and enables that channel, records markers,
disables it (an enabled analytic channel cannot be read) and keeps the
events that mention root\wmi or ASUS.

Run as Administrator. The channel names the class and the method (DEVS,
DSTS, ...); whether it also carries the arguments is unknown (about 60 % that
it does not). If it does not, the trace still says *which* WMI method a
setting uses, and atk_query.py before/after says which value it changed.
"""

import argparse
import datetime
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zenduo_re  # noqa: E402

CHANNEL = "Microsoft-Windows-WMI-Activity/Trace"
INTERESTING = re.compile(r"root\\wmi|asus|atk|wmnb", re.IGNORECASE)
NS = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}


def wevtutil(*args, check=True):
    return subprocess.run(["wevtutil", *args], capture_output=True, text=True, check=check)


def parse_events(xml_text):
    """Events from `wevtutil qe /f:xml` output (a sequence, not one document)."""
    root = ET.fromstring(f"<Events>{xml_text}</Events>")
    events = []
    for event in root.findall("e:Event", NS):
        created = event.find("e:System/e:TimeCreated", NS)
        execution = event.find("e:System/e:Execution", NS)
        data = {}
        # EventData carries <Data Name="...">; UserData (what WMI-Activity
        # uses) carries its fields as plain leaf elements. Take both.
        for section in list(event)[1:]:
            for item in section.iter():
                if list(item):
                    continue
                name = item.get("Name") or item.tag.split("}")[-1]
                if item.text and item.text.strip():
                    data[name] = item.text.strip()
        time_text = created.get("SystemTime", "") if created is not None else ""
        events.append({
            "time": time_text,
            "ts": system_time_to_epoch(time_text),
            "event_pid": execution.get("ProcessID") if execution is not None else "",
            "data": data,
        })
    return events


def system_time_to_epoch(text):
    """'2026-10-30T11:24:02.1234567Z' -> seconds since the epoch (UTC)."""
    match = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?Z?$", text or "")
    if not match:
        return 0.0
    whole = datetime.datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    whole = whole.replace(tzinfo=datetime.timezone.utc).timestamp()
    return whole + float(match.group(2) or 0)


def interesting(events):
    return [e for e in events if INTERESTING.search(" ".join(e["data"].values()))]


class WmiTrace:
    """Enables the WMI-Activity analytic channel; stop() reads it back.

    An enabled analytic channel cannot be read, so nothing is visible until
    stop(); the events are matched to markers afterwards by their time.
    """

    def start(self):
        """Clear and enable the channel. Returns an error string, or None."""
        wevtutil("sl", CHANNEL, "/e:false", check=False)
        wevtutil("cl", CHANNEL, check=False)
        enabled = wevtutil("sl", CHANNEL, "/e:true", "/q:true", check=False)
        if enabled.returncode:
            return f"could not enable {CHANNEL}: {enabled.stderr.strip()} (Administrator?)"
        return None

    def stop(self, out):
        """Disable, export to `out`, write wmi.jsonl; returns the kept events."""
        wevtutil("sl", CHANNEL, "/e:false", check=False)
        exported = wevtutil("qe", CHANNEL, "/f:xml", check=False).stdout
        with open(os.path.join(out, "wmi-activity.xml"), "w", encoding="utf-8") as f:
            f.write(exported)
        log = zenduo_re.JsonLog(os.path.join(out, "wmi.jsonl"))
        kept = interesting(parse_events(exported))
        for event in kept:
            log.write({"type": "wmi", **event})
        log.close()
        return kept


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scenario", choices=sorted(zenduo_re.SCENARIOS))
    parser.add_argument("--steps", help=zenduo_re.STEPS_HELP)
    parser.add_argument("--parse", help="decode a saved export instead of recording")
    parser.add_argument("--out", help="parent folder for the session (default windows/captures)")
    args = parser.parse_args(argv)
    only = zenduo_re.selected_steps(args)

    if args.parse:
        with open(args.parse, encoding="utf-8-sig") as f:     # PowerShell adds a BOM
            for e in interesting(parse_events(f.read())):
                print(e["time"], e["data"].get("ClientProcessId", "?"),
                      e["data"].get("Operation", e["data"]))
        return 0

    zenduo_re.require_windows("wmi_trace")
    out = zenduo_re.session_dir("wmi", args.out)
    markers = zenduo_re.JsonLog(os.path.join(out, "markers.jsonl"))
    trace = WmiTrace()
    error = trace.start()
    if error:
        sys.exit(f"wmi_trace: {error}")
    try:
        zenduo_re.run_steps(markers, args.scenario, only=only)
    finally:
        kept = trace.stop(out)
    for event in kept:
        print(event["time"], event["data"].get("Operation", ""))
    print(f"{len(kept)} WMI operations kept; written {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
