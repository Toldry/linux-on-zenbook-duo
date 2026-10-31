"""windows/ — the decoders that turn Windows captures into Linux facts.

The capture tools only run on Windows; what they record is read by these
functions, on either OS, so these are what must be right.
"""

import io
import json
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "windows"))
import analyze  # noqa: E402
import snapshot  # noqa: E402
import speaker_response  # noqa: E402
import usb_capture  # noqa: E402
import wmi_trace  # noqa: E402
import zenduo_re  # noqa: E402

DEVS = 0x53564544
DSTS = 0x53545344


class AtkDecoding(unittest.TestCase):
    def test_devs_names_method_device_and_value(self):
        buf = zenduo_re.atk_call_buffer(DEVS, struct.pack("<II", 0x00110019, 2))
        self.assertEqual(buf[:8], b"DEVS\x08\x00\x00\x00")
        d = zenduo_re.decode_atk_call(buf, struct.pack("<I", 1))
        self.assertEqual(d["method"], "DEVS")
        self.assertEqual(d["device_id"], "0x00110019")
        self.assertEqual(d["device"], "THROTTLE_THERMAL_POLICY_VIVO")
        self.assertEqual(d["value"], "0x00000002")
        self.assertEqual(d["returned"], "0x00000001")

    def test_dsts_presence(self):
        buf = zenduo_re.atk_call_buffer(DSTS, struct.pack("<I", 0x00120057))
        present = zenduo_re.decode_atk_call(buf, struct.pack("<I", 0x00010050))
        self.assertTrue(present["present"])
        absent = zenduo_re.decode_atk_call(buf, struct.pack("<I", 0xFFFFFFFE))
        self.assertFalse(absent["present"])

    def test_short_input_does_not_raise(self):
        self.assertIn("error", zenduo_re.decode_atk_call(b"DEV"))

    def test_linux_equivalent_uses_debugfs(self):
        d = zenduo_re.decode_atk_call(
            zenduo_re.atk_call_buffer(DEVS, struct.pack("<II", 0x00120057, 80)))
        lines = zenduo_re.linux_equivalent_atk(d)
        self.assertIn("0x00120057", lines[0])
        self.assertIn("0x00000050", lines[1])
        self.assertTrue(lines[2].endswith("/devs"))
        self.assertIsNone(zenduo_re.linux_equivalent_atk({"method": "INIT"}))

    def test_decode_ioctl_routes_by_code(self):
        self.assertEqual(zenduo_re.decode_ioctl(0x00222408, b"", b"\x6a\0\0\0")["event"], "0x6a")
        hid = zenduo_re.decode_ioctl(0x000B0191, b"", bytes([0x5A, 0xBA, 0xC5, 0xC4, 2]))
        self.assertEqual(hid["ioctl"], "IOCTL_HID_SET_FEATURE")
        self.assertIn("level 2", hid["meaning"])
        self.assertEqual(zenduo_re.decode_ioctl(0x1234, b"", b"")["kind"], "other")


class HidMeanings(unittest.TestCase):
    def test_handshake_padded_still_matches(self):
        data = bytes([0x5A]) + b"ASUS Tech.Inc.\0" + b"\0\0"
        self.assertIn("handshake", zenduo_re.describe_feature(data))

    def test_unknown_is_flagged_new(self):
        self.assertIn("new", zenduo_re.describe_feature(bytes([0x5A, 0x01, 0x02])))

    def test_vendor_inputs(self):
        self.assertEqual(zenduo_re.describe_vendor_input(bytes([0x5A, 0xC7])),
                         "keyboard backlight key")
        self.assertEqual(zenduo_re.describe_vendor_input(bytes([0x5A, 0x00])), "key release")
        self.assertIn("0x99", zenduo_re.describe_vendor_input(bytes([0x5A, 0x99])))
        self.assertIsNone(zenduo_re.describe_vendor_input(bytes([0x01, 0x02])))


class FakeKeys:
    """Key presses from a list; None (nothing pressed) once it runs out."""

    def __init__(self, keys=(), lines=()):
        self.keys, self.lines = list(keys), list(lines)

    def get(self, timeout=None):
        if self.keys:
            return self.keys.pop(0)
        if timeout is None:
            return "q"          # a blocking wait with nothing left: stop
        return None

    def line(self):
        return self.lines.pop(0) if self.lines else ""


class Markers(unittest.TestCase):
    def run_steps(self, scenario, keys, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            log = zenduo_re.JsonLog(os.path.join(tmp, "m.jsonl"))
            out = io.StringIO()
            n = zenduo_re.run_steps(log, scenario, keys=keys, out=out, **kwargs)
            log.close()
            return n, zenduo_re.read_jsonl(log.path), out.getvalue()

    def test_free_form_markers_and_labels(self):
        n, records, _ = self.run_steps(None, FakeKeys(lines=["fan whisper\n", "\n", "q\n"]))
        self.assertEqual(n, 1)
        self.assertEqual([m["label"] for m in records],
                         ["capture started", "fan whisper", "capture stopped"])

    def test_marker_is_written_when_the_step_is_shown(self):
        # Step 1 of power is an action: shown, marked, then Enter = done.
        # Step 2 is skipped (it gets a "skipped:" marker), then q stops.
        n, records, out = self.run_steps("power", FakeKeys(["enter", "s", "q"]))
        self.assertEqual(n, 1)
        steps = [(m.get("step"), m.get("skipped", False)) for m in records if "step" in m]
        self.assertEqual(steps, [(1, False), (2, False), (2, True), (3, False)])
        self.assertIn("STEP 1/", out)
        self.assertNotIn("\x1b[", out)       # no colour codes into a non-console

    def test_keyboard_state_step_ends_by_itself(self):
        states = iter(["none", "none", "docked"])
        n, records, out = self.run_steps(
            "keyboard-docked", FakeKeys([None, None, "q"]),
            detect=lambda: next(states, "docked"))
        self.assertEqual(n, 1)                # step 1 done without a key, then q
        self.assertIn("seen: keyboard docked", out)
        self.assertIn("must be DOCKED", out)

    def test_keyboard_moved_mid_run_holds_the_step(self):
        # Step 3 (F4) finds the keyboard on Bluetooth in a docked run: the
        # step waits, the keyboard comes back, then the step needs its Enter.
        states = iter(["docked", "docked", "bluetooth", "bluetooth", "docked"])
        keys = FakeKeys(["s", None, "enter", "q"])     # skip the countdown
        n, records, out = self.run_steps("keyboard-docked", keys,
                                         detect=lambda: next(states, "docked"))
        self.assertIn("not where this run needs it", out)
        self.assertEqual(n, 2)                # dock, F4; the countdown was skipped

    def test_without_detection_the_state_step_asks_for_enter(self):
        n, _, out = self.run_steps("attach", FakeKeys(["enter", "q"]))
        self.assertEqual(n, 1)
        self.assertIn("Enter once the keyboard is DOCKED", out)

    def test_countdown_runs_out_on_its_own(self):
        quiet = FakeKeys(["enter", "enter"] + [None] * 10 + ["q"])
        n, _, out = self.run_steps("power", quiet)
        self.assertEqual(n, 3)                # charger, open MyASUS, 10 s baseline
        self.assertIn("hands off:   1 s left", out)

    def test_after_step_summary_is_shown(self):
        n, _, out = self.run_steps("power", FakeKeys(["enter", "q"]),
                                   after_step=lambda i: f"caught {i}")
        self.assertIn("✓ caught 1", out)

    def test_every_scenario_is_well_formed(self):
        for name, plan in zenduo_re.SCENARIOS.items():
            self.assertTrue(plan["steps"], name)
            self.assertIn(plan["keyboard"], (None, *zenduo_re.KEYBOARD_STATES), name)
            for current in plan["steps"]:
                self.assertIn(current.needs, (None, *zenduo_re.KEYBOARD_STATES), name)
                self.assertTrue(current.do.endswith((".", ")")), current.do)
        self.assertEqual(zenduo_re.SCENARIOS["keyboard-docked"]["steps"][0].needs, "docked")
        self.assertEqual(zenduo_re.SCENARIOS["keyboard-bluetooth"]["steps"][1].needs,
                         "bluetooth")

    def test_keyboard_transport_from_paths(self):
        usb = r"\\?\hid#vid_0b05&pid_1b2c&mi_04&col01#7&1&0000#{4d1e55b2-f16f}"
        bt = (r"\\?\hid#{00001124-0000-1000-8000-00805f9b34fb}_vid&00020b05"
              r"_pid&1b2d&col03#8&2&0002#{4d1e55b2-f16f}")
        other = r"\\?\hid#vid_046d&pid_c52b&mi_02#7&2&0000#{4d1e55b2-f16f}"
        self.assertEqual(zenduo_re.keyboard_transport([other, usb]), "docked")
        self.assertEqual(zenduo_re.keyboard_transport([bt, other]), "bluetooth")
        self.assertEqual(zenduo_re.keyboard_transport([other]), "none")
        self.assertTrue(zenduo_re.is_bluetooth_path(bt))
        self.assertFalse(zenduo_re.is_bluetooth_path(usb))

    def test_event_log_counts_per_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = zenduo_re.EventLog(os.path.join(tmp, "events.log"))
            events.add("ATK call", "a")
            events.add("ATK call", "b")
            events.add(None, "hooked x")
            self.assertEqual(zenduo_re.count_summary(events.take_counts()), "2 ATK calls")
            events.add("HID write", "c")
            self.assertEqual(zenduo_re.count_summary(events.take_counts()), "1 HID write")
            self.assertEqual(zenduo_re.count_summary(events.take_counts()),
                             "nothing captured")
            events.close()
            events.add("ATK call", "after close is ignored")
            self.assertEqual(events.total["ATK call"], 2)

    def test_label_for(self):
        markers = [{"ts": 10, "label": "a"}, {"ts": 20, "label": "b"}]
        self.assertEqual(zenduo_re.label_for(5, markers), "")
        self.assertEqual(zenduo_re.label_for(15, markers), "a")
        self.assertEqual(zenduo_re.label_for(20, markers), "b")


def usbpcap_packet(irp, completion, bus, device, endpoint, transfer, payload,
                   stage=None, status=0):
    header_len = 28 if transfer == 2 else 27
    header = struct.pack("<HQIHBHHBBI", header_len, irp, status, 0, int(completion),
                         bus, device, endpoint, transfer, len(payload))
    if transfer == 2:
        header += bytes([stage])
    return header + payload


def pcap(packets):
    blob = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 249)
    for ts, data in packets:
        blob += struct.pack("<IIII", int(ts), int((ts % 1) * 1e6), len(data), len(data)) + data
    return blob


def pcapng(packets):
    def block(kind, body):
        body += b"\0" * (-len(body) % 4)
        return struct.pack("<II", kind, len(body) + 12) + body + struct.pack("<I", len(body) + 12)
    blob = block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    blob += block(1, struct.pack("<HHI", 249, 0, 0))
    for ts, data in packets:
        ticks = int(ts * 1e6)
        blob += block(6, struct.pack("<IIIII", 0, ticks >> 32, ticks & 0xFFFFFFFF,
                                     len(data), len(data)) + data)
    return blob


def keyboard_traffic():
    descriptor = bytes([0x12, 0x01, 0x00, 0x02, 0, 0, 0, 64]) + struct.pack(
        "<HHH", 0x0B05, 0x1B2C, 0x0100) + bytes([1, 2, 0, 1])
    get_descriptor = struct.pack("<BBHHH", 0x80, 6, 0x0100, 0, 18)
    feature = bytes([0x5A, 0xBA, 0xC5, 0xC4, 0x03]) + bytes(12)
    set_report = struct.pack("<BBHHH", 0x21, 0x09, 0x035A, 4, len(feature))
    return [
        (100.0, usbpcap_packet(1, False, 1, 5, 0x80, 2, get_descriptor, stage=0)),
        (100.1, usbpcap_packet(1, True, 1, 5, 0x80, 2, descriptor, stage=3)),
        (101.0, usbpcap_packet(2, False, 1, 5, 0x00, 2, set_report + feature, stage=0)),
        (101.1, usbpcap_packet(2, True, 1, 5, 0x00, 2, b"", stage=3)),
        (102.0, usbpcap_packet(3, True, 1, 5, 0x84, 1, bytes([0x5A, 0xC7]))),
        (103.0, usbpcap_packet(4, False, 1, 5, 0x00, 2, set_report + feature, stage=0)),
        (103.1, usbpcap_packet(4, True, 1, 5, 0x00, 2, b"", stage=3, status=0xC0000004)),
    ]


class UsbParsing(unittest.TestCase):
    def check(self, events):
        sets = [e for e in events if e["request"] == "SET_REPORT"]
        self.assertEqual(len(sets), 2)
        self.assertEqual(sets[0]["device"], "0b05:1b2c")
        self.assertEqual(sets[0]["report_type"], "feature")
        self.assertEqual(sets[0]["report_id"], "0x5a")
        self.assertIn("level 3", sets[0]["meaning"])
        self.assertFalse(sets[0]["stalled"])
        self.assertTrue(sets[1]["stalled"])
        inputs = [e for e in events if e["request"] == "INTERRUPT_IN"]
        self.assertEqual(inputs[0]["meaning"], "keyboard backlight key")

    def test_pcap(self):
        with tempfile.NamedTemporaryFile(suffix=".pcap", delete=False) as f:
            f.write(pcap(keyboard_traffic()))
        try:
            packets = analyze.read_packets(f.name)
            self.assertAlmostEqual(packets[0][0], 100.0)
            self.check(analyze.usb_events(packets))
        finally:
            os.unlink(f.name)

    def test_pcapng(self):
        with tempfile.NamedTemporaryFile(suffix=".pcapng", delete=False) as f:
            f.write(pcapng(keyboard_traffic()))
        try:
            packets = analyze.read_packets(f.name)
            self.assertAlmostEqual(packets[2][0], 101.0, places=3)
            self.check(analyze.usb_events(packets))
        finally:
            os.unlink(f.name)

    def test_truncated_pcap_keeps_whole_records(self):
        blob = pcap(keyboard_traffic())
        with tempfile.NamedTemporaryFile(suffix=".pcap", delete=False) as f:
            f.write(blob[:-5])
        try:
            self.assertEqual(len(analyze.read_packets(f.name)), len(keyboard_traffic()) - 1)
        finally:
            os.unlink(f.name)

    def test_other_vendors_dropped(self):
        events = [{"device": "046d:c52b", "data": "5a00"},
                  {"device": "unknown", "data": "0100"},
                  {"device": "unknown", "data": "5ac7"}]
        self.assertEqual([analyze.keep_usb_event(e) for e in events], [False, False, True])


class Sessions(unittest.TestCase):
    def test_timeline_groups_by_marker_and_collects_linux_hints(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "markers.jsonl"), "w") as f:
                f.write(json.dumps({"type": "marker", "label": "fan whisper", "ts": 100.5}) + "\n")
                f.write(json.dumps({"type": "marker", "label": "kbd", "ts": 101.5}) + "\n")
            call = {"type": "call", "api": "DeviceIoControl", "ts": 100.7, "code": 0x0022240C,
                    "decoded": {"kind": "atk", "method": "DEVS"}, "linux": ["echo x"]}
            with open(os.path.join(tmp, "hooks.jsonl"), "w") as f:
                f.write(json.dumps(call) + "\n")
                f.write(json.dumps({"type": "call", "api": "ready", "ts": 100.6}) + "\n")
            with open(os.path.join(tmp, "usb.pcap"), "wb") as f:
                f.write(pcap(keyboard_traffic()))
            groups = analyze.timeline([tmp])
            labels = [g["label"] for g in groups]
            self.assertEqual(labels, ["fan whisper", "kbd"])
            self.assertEqual(analyze.linux_hints(groups[0]["events"]), ["echo x"])
            for group in groups:
                for event in group["events"]:
                    self.assertIsInstance(analyze.describe(event), str)

    def test_atk_changes_per_step(self):
        before = {0x00120075: 0x00010000, 0x00120057: 0x00010050, 0x00050001: None}
        after = {0x00120075: 0x00010001, 0x00120057: 0x00010050, 0x00050001: 0x00010000,
                 0x00999999: 5}         # first seen now: a baseline, not a change
        changes = zenduo_re.atk_changes(before, after)
        self.assertEqual([c["device_id"] for c in changes], ["0x00050001", "0x00120075"])
        self.assertEqual(zenduo_re.describe_atk_change(changes[1]),
                         "THROTTLE_THERMAL_POLICY (0x00120075): 0x00010000 -> 0x00010001")
        self.assertEqual(changes[0]["before"], "failed")

    def test_atk_change_records_join_the_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, records in {
                "markers.jsonl": [{"type": "marker", "label": "fan", "ts": 1}],
                "atk-changes.jsonl": [{"type": "atk-change", "ts": 2,
                                       "device_id": "0x00120075",
                                       "device": "THROTTLE_THERMAL_POLICY",
                                       "before": "0x00010000", "after": "0x00010001",
                                       "linux": ["echo 0x00120075 | sudo tee x"]}],
            }.items():
                with open(os.path.join(tmp, name), "w") as f:
                    f.writelines(json.dumps(r) + "\n" for r in records)
            groups = analyze.timeline([tmp])
            self.assertEqual(groups[0]["label"], "fan")
            self.assertIn("BIOS value changed: THROTTLE_THERMAL_POLICY",
                          analyze.describe(groups[0]["events"][0]))
            self.assertEqual(analyze.linux_hints(groups[0]["events"]),
                             ["echo 0x00120075 | sudo tee x"])

    def test_atk_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, value in (("a", "0x00010000"), ("b", "0x00010002")):
                with open(os.path.join(tmp, name), "w") as f:
                    json.dump([{"device_id": "0x00110019", "device": "VIVO", "returned": value},
                               {"device_id": "0x1", "device": "x", "returned": "0x0"}], f)
            lines = analyze.atk_diff(os.path.join(tmp, "a"), os.path.join(tmp, "b"))
            self.assertEqual(lines, ["0x00110019 VIVO: 0x00010000 -> 0x00010002"])


class OtherTools(unittest.TestCase):
    def test_snapshot_diff(self):
        before = {"registry": {"k\\a": "4:1", "k\\gone": "1:'x'"},
                  "files": {"c.json": {"size": 9, "sha256": "aa", "text": '{"fan": 0}'}}}
        after = {"registry": {"k\\a": "4:2", "k\\new": "1:'y'"},
                 "files": {"c.json": {"size": 9, "sha256": "bb", "text": '{"fan": 2}'}}}
        lines = snapshot.diff_snapshots(before, after)
        self.assertIn("~ registry: k\\a: 4:1 -> 4:2", lines)
        self.assertIn("- registry: k\\gone", lines)
        self.assertIn("+ registry: k\\new = 1:'y'", lines)
        self.assertIn('    +{"fan": 2}', lines)

    def test_extcap_interfaces(self):
        text = ("extcap {version=1.5.4.0}\n"
                "interface {value=\\\\.\\USBPcap1}{display=USBPcap1}\n"
                "interface {value=\\\\.\\USBPcap2}{display=USBPcap2}\n")
        self.assertEqual(usb_capture.parse_extcap_interfaces(text),
                         ["\\\\.\\USBPcap1", "\\\\.\\USBPcap2"])

    def test_wmi_events(self):
        xml = ('<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">'
               '<System><TimeCreated SystemTime="2026-10-30T11:24:02.5000000Z"/>'
               '<Execution ProcessID="1200"/></System><UserData><Operation_New xmlns="x">'
               '<Operation>Start IWbemServices::ExecMethod - root\\wmi :'
               ' AsusAtkWmi_WMNB::DEVS</Operation><ClientProcessId>4321</ClientProcessId>'
               '</Operation_New></UserData></Event>'
               '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">'
               '<System><TimeCreated SystemTime="2026-10-30T11:24:03Z"/></System>'
               '<UserData><Op xmlns="x"><Operation>root\\cimv2 : Win32_Process</Operation>'
               '</Op></UserData></Event>')
        events = wmi_trace.parse_events(xml)
        self.assertEqual(len(events), 2)
        kept = wmi_trace.interesting(events)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["data"]["ClientProcessId"], "4321")
        self.assertAlmostEqual(kept[0]["ts"] % 60, 2.5)


try:
    import numpy
except ImportError:
    numpy = None


@unittest.skipUnless(numpy, "numpy not installed")
class SpeakerAnalysis(unittest.TestCase):
    def test_pink_noise_is_flat_per_third_octave(self):
        # Pink noise has equal energy per fractional octave: the bands sit
        # within a few dB of each other, which is what makes it the stimulus.
        signal = speaker_response.pink_noise(numpy, seconds=4.0)
        levels = speaker_response.relative(speaker_response.band_levels(numpy, signal))
        self.assertEqual(levels[630], 0.0)
        self.assertLess(max(abs(v) for v in levels.values()), 3.0)

    def test_compare_subtracts_shapes(self):
        a = {"label": "w", "levels": {"-20.0": {"relative_db": {"100": -10.0, "630": 0.0}}}}
        b = {"label": "l", "levels": {"-20.0": {"relative_db": {"100": -25.0, "630": 0.0}},
                                      "-30.0": {"relative_db": {"100": 0.0}}}}
        self.assertEqual(speaker_response.compare_curves(a, b), {"-20.0": {100: 15.0, 630: 0.0}})


if __name__ == "__main__":
    unittest.main()
