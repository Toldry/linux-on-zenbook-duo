#!/usr/bin/env python3
r"""Turn capture sessions into one timeline per action, with Linux equivalents.

    python3 analyze.py windows/captures/20261030-120000-hooks [more sessions...]
    python3 analyze.py --json timeline.json <sessions...>
    python3 analyze.py atk-diff before.json after.json   (two atk_query --json files)

Runs anywhere (stdlib only): copy the captures folder to the Ubuntu side and
read them there. Each session folder may hold markers.jsonl plus any of
hooks.jsonl (hook_asus), hid-input.jsonl (hid_probe listen), wmi.jsonl
(wmi_trace) and *.pcap / *.pcapng (usb_capture, or Wireshark). Events from
all of them are merged by wall-clock time and grouped under the marker that
was current, so "MyASUS: fan profile Whisper" lists the ATK calls, the WMI
operations and the USB transfers that followed it.

USB parsing knows the USBPcap link type only (249), which is what USBPcap and
Wireshark on Windows write. It keeps HID class requests (SET_REPORT and
GET_REPORT) and interrupt-IN data, and names the device from the descriptors
USBPcap injects at the start (usb_capture passes --inject-descriptors).
"""

import argparse
import glob
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zenduo_re  # noqa: E402

LINKTYPE_USBPCAP = 249
REPORT_TYPES = {1: "input", 2: "output", 3: "feature"}


# ── pcap / pcapng ────────────────────────────────────────────────────────────

def read_packets(path):
    """(timestamp, linktype, bytes) for each packet of a pcap or pcapng file.

    Tolerates a truncated last record: USBPcapCMD is stopped by being killed.
    """
    with open(path, "rb") as f:
        blob = f.read()
    if blob[:4] == b"\x0a\x0d\x0d\x0a":
        return list(_read_pcapng(blob))
    return list(_read_pcap(blob))


def _read_pcap(blob):
    if len(blob) < 24:
        return
    magic = blob[:4]
    formats = {b"\xd4\xc3\xb2\xa1": ("<", 1e-6), b"\xa1\xb2\xc3\xd4": (">", 1e-6),
               b"\x4d\x3c\xb2\xa1": ("<", 1e-9), b"\xa1\xb2\x3c\x4d": (">", 1e-9)}
    if magic not in formats:
        raise ValueError("not a pcap file")
    endian, unit = formats[magic]
    linktype = struct.unpack_from(endian + "I", blob, 20)[0]
    offset = 24
    while offset + 16 <= len(blob):
        seconds, fraction, caplen, _ = struct.unpack_from(endian + "IIII", blob, offset)
        offset += 16
        if offset + caplen > len(blob):
            break
        yield seconds + fraction * unit, linktype, blob[offset:offset + caplen]
        offset += caplen


def _read_pcapng(blob):
    endian = "<"
    interfaces = []                  # (linktype, seconds per tick)
    offset = 0
    while offset + 12 <= len(blob):
        block_type = struct.unpack_from(endian + "I", blob, offset)[0]
        if block_type == 0x0A0D0D0A:
            endian = "<" if blob[offset + 8:offset + 12] == b"\x4d\x3c\x2b\x1a" else ">"
            interfaces = []
        length = struct.unpack_from(endian + "I", blob, offset + 4)[0]
        if length < 12 or offset + length > len(blob):
            break
        body = blob[offset + 8:offset + length - 4]
        if block_type == 1:                                   # interface description
            linktype = struct.unpack_from(endian + "H", body, 0)[0]
            interfaces.append([linktype, 1e-6])
            opt = 8
            while opt + 4 <= len(body):
                code, size = struct.unpack_from(endian + "HH", body, opt)
                if code == 0:
                    break
                if code == 9 and size >= 1:                   # if_tsresol
                    value = body[opt + 4]
                    interfaces[-1][1] = 2 ** -(value & 0x7F) if value & 0x80 else 10 ** -value
                opt += 4 + (size + 3) // 4 * 4
        elif block_type == 6 and interfaces:                  # enhanced packet
            iface, high, low, caplen = struct.unpack_from(endian + "IIII", body, 0)
            if iface < len(interfaces):
                linktype, tick = interfaces[iface]
                yield ((high << 32) | low) * tick, linktype, body[20:20 + caplen]
        offset += length


# ── USBPcap ──────────────────────────────────────────────────────────────────

def parse_usbpcap(data):
    """The USBPCAP_BUFFER_PACKET_HEADER fields (usbpcap.h) and the payload."""
    if len(data) < 27:
        return None
    (header_len, irp, status, function, info, bus, device, endpoint, transfer,
     length) = struct.unpack_from("<HQIHBHHBBI", data, 0)
    packet = {"irp": irp, "status": status, "function": function,
              "completion": bool(info & 1), "bus": bus, "device": device,
              "endpoint": endpoint, "transfer": transfer,
              "payload": data[header_len:header_len + length]}
    packet["stage"] = data[27] if transfer == 2 and header_len >= 28 else None
    return packet


def usb_events(packets):
    """HID requests and interrupt-IN reports from (ts, linktype, bytes) packets."""
    devices = {}                     # (bus, device) -> "vvvv:pppp"
    open_controls = {}
    events = []

    def finish(record):
        setup = record["setup"]
        if len(setup) < 8:
            return
        request_type, request, value, index, length = struct.unpack("<BBHHH", setup[:8])
        if request_type == 0x80 and request == 6 and value >> 8 == 1:      # device descriptor
            desc = record["in"]
            if len(desc) >= 12 and desc[1] == 1:
                vid, pid = struct.unpack_from("<HH", desc, 8)
                devices[record["where"]] = f"{vid:04x}:{pid:04x}"
            return
        if request_type & 0x60 != 0x20 or request not in (0x01, 0x09):   # class requests
            return
        kind = "SET_REPORT" if request == 0x09 else "GET_REPORT"
        data = record["out"] if kind == "SET_REPORT" else record["in"]
        event = {"type": "usb", "ts": record["ts"], "request": kind,
                 "report_type": REPORT_TYPES.get(value >> 8, str(value >> 8)),
                 "report_id": f"0x{value & 0xFF:02x}", "interface": index,
                 "length": length, "data": data.hex(), "where": record["where"],
                 "status": f"0x{record['status']:08x}",
                 "stalled": record["status"] != 0}
        if event["report_type"] == "feature" and data:
            event["meaning"] = zenduo_re.describe_feature(data)
        events.append(event)

    for ts, linktype, raw in packets:
        if linktype != LINKTYPE_USBPCAP:
            continue
        p = parse_usbpcap(raw)
        if p is None:
            continue
        where = (p["bus"], p["device"])
        if p["transfer"] == 2:                                          # control
            key = (p["irp"], where)
            if not p["completion"]:
                if p["stage"] == 0 or key not in open_controls:
                    if key in open_controls:
                        finish(open_controls.pop(key))
                    open_controls[key] = {"ts": ts, "where": where, "setup": p["payload"][:8],
                                          "out": p["payload"][8:], "in": b"", "status": 0}
                else:
                    open_controls[key]["out"] += p["payload"]
            elif key in open_controls:
                record = open_controls[key]
                record["status"] = p["status"]
                if p["payload"]:
                    record["in"] += p["payload"]
                if p["stage"] != 1:
                    finish(open_controls.pop(key))
        elif p["transfer"] == 1 and p["completion"] and p["endpoint"] & 0x80 and p["payload"]:
            event = {"type": "usb", "ts": ts, "request": "INTERRUPT_IN",
                     "endpoint": f"0x{p['endpoint']:02x}", "data": p["payload"].hex(),
                     "where": where}
            meaning = zenduo_re.describe_vendor_input(p["payload"])
            if meaning:
                event["meaning"] = meaning
            events.append(event)
    for record in open_controls.values():
        finish(record)
    for event in events:
        event["device"] = devices.get(event["where"], "unknown")
        event["where"] = f"bus {event['where'][0]} device {event['where'][1]}"
    return [e for e in events if keep_usb_event(e)]


def keep_usb_event(event):
    """ASUS devices, and unknown ones only when they look like the keyboard."""
    if event["device"].startswith("0b05:"):
        return True
    return event["device"] == "unknown" and event["data"].startswith("5a")


# ── Sessions ─────────────────────────────────────────────────────────────────

def load_session(folder):
    markers, events = [], []
    for name in ("markers.jsonl",):
        path = os.path.join(folder, name)
        if os.path.exists(path):
            markers = [m for m in zenduo_re.read_jsonl(path) if m.get("type") == "marker"]
    for name in ("hooks.jsonl", "hid-input.jsonl", "wmi.jsonl"):
        path = os.path.join(folder, name)
        if os.path.exists(path):
            events += [e for e in zenduo_re.read_jsonl(path)
                       if e.get("type") in ("call", "input", "wmi")
                       and e.get("api") not in ("ready",)]
    for path in glob.glob(os.path.join(folder, "*.pcap*")):
        try:
            events += usb_events(read_packets(path))
        except (ValueError, struct.error) as error:
            print(f"skipped {path}: {error}", file=sys.stderr)
    return markers, events


def describe(event):
    kind = event.get("type")
    if kind == "call":
        api = event.get("api")
        if api == "DeviceIoControl":
            d = event.get("decoded", {})
            if d.get("kind") == "atk":
                return (f"ATK {d.get('method')} {d.get('device_id', '')} ({d.get('device', '')})"
                        f" value={d.get('value', '-')} -> {d.get('returned', '?')}"
                        f"  [{event.get('process')}]")
            return f"{d.get('ioctl', d.get('kind'))} in={event.get('in')} out={event.get('out')}"
        if api == "CreateFileW":
            return f"open {event.get('path')}  [{event.get('process')}]"
        data = event.get("in") if "Set" in (api or "") else event.get("out")
        return f"{api} {data}  {event.get('meaning', '')}  [{event.get('process')}]"
    if kind == "input":
        return f"HID input {event.get('data')}  {event.get('meaning', '')}"
    if kind == "wmi":
        data = event.get("data", {})
        return f"WMI {data.get('Operation', data)}  pid {data.get('ClientProcessId', '?')}"
    if kind == "usb":
        if event["request"] == "INTERRUPT_IN":
            return f"USB in {event['data']}  {event.get('meaning', '')}  {event['device']}"
        stall = "  STALLED" if event.get("stalled") else ""
        return (f"USB {event['request']} {event['report_type']} {event['report_id']}"
                f" if{event['interface']} {event['data']}  {event.get('meaning', '')}"
                f"{stall}  {event['device']}")
    return json.dumps(event)


def timeline(sessions):
    markers, events = [], []
    for folder in sessions:
        m, e = load_session(folder)
        markers += m
        events += e
    markers.sort(key=lambda m: m.get("ts", 0))
    events.sort(key=lambda e: e.get("ts", 0))
    groups = []
    for event in events:
        label = zenduo_re.label_for(event.get("ts", 0), markers)
        if not groups or groups[-1]["label"] != label:
            groups.append({"label": label, "events": []})
        groups[-1]["events"].append(event)
    return groups


def linux_hints(events):
    """Unique Linux replays for the events of one group."""
    hints = []
    for event in events:
        for line in event.get("linux", []):
            if line not in hints:
                hints.append(line)
    return hints


def atk_diff(before_path, after_path):
    with open(before_path, encoding="utf-8") as f:
        before = {r["device_id"]: r for r in json.load(f)}
    with open(after_path, encoding="utf-8") as f:
        after = {r["device_id"]: r for r in json.load(f)}
    lines = []
    for device_id in sorted(set(before) | set(after)):
        old = before.get(device_id, {}).get("returned")
        new = after.get(device_id, {}).get("returned")
        if old != new:
            name = (after.get(device_id) or before.get(device_id)).get("device", "")
            lines.append(f"{device_id} {name}: {old} -> {new}")
    return lines


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["atk-diff"]:
        if len(argv) != 3:
            sys.exit("usage: analyze.py atk-diff before.json after.json")
        print("\n".join(atk_diff(argv[1], argv[2])) or "no difference")
        return 0
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("sessions", nargs="+", help="capture session folders")
    parser.add_argument("--json", help="also write the grouped timeline here")
    parser.add_argument("--quiet-opens", action="store_true", help="hide CreateFileW lines")
    args = parser.parse_args(argv)
    groups = timeline(args.sessions)
    for group in groups:
        print(f"\n== {group['label'] or '(before the first marker)'}")
        for event in group["events"]:
            if args.quiet_opens and event.get("api") == "CreateFileW":
                continue
            print("   " + describe(event))
        hints = linux_hints(group["events"])
        if hints:
            print("   Linux replay (root, by hand, one at a time):")
            for line in hints:
                print("     " + line)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(groups, f, indent=2, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
