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


class Markers(unittest.TestCase):
    def test_free_form_markers_and_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = zenduo_re.JsonLog(os.path.join(tmp, "m.jsonl"))
            n = zenduo_re.run_markers(log, None, out=io.StringIO(),
                                      inp=io.StringIO("fan whisper\n\nq\n"))
            log.close()
            self.assertEqual(n, 1)
            labels = [m["label"] for m in zenduo_re.read_jsonl(log.path)]
            self.assertEqual(labels, ["capture started", "fan whisper", "capture stopped"])

    def test_scenario_skip_and_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = zenduo_re.JsonLog(os.path.join(tmp, "m.jsonl"))
            n = zenduo_re.run_markers(log, "power", out=io.StringIO(),
                                      inp=io.StringIO("\ns\nq\n\n"))
            log.close()
            self.assertEqual(n, 1)
            steps = [m.get("step") for m in zenduo_re.read_jsonl(log.path) if "step" in m]
            self.assertEqual(steps, [1])

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
