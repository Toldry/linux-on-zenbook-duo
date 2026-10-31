"""lib/battery.py — the laptop and keyboard battery, from fake sysfs and text."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import battery  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class Laptop(unittest.TestCase):
    def test_capacity_and_status(self):
        with tempfile.TemporaryDirectory() as root:
            write(f"{root}/BAT0/capacity", "57\n")
            write(f"{root}/BAT0/status", "Discharging\n")
            self.assertEqual(battery.laptop(root), (57, "Discharging"))
            self.assertEqual(battery.laptop_text(battery.laptop(root)), "57 %, on battery")

    def test_the_charge_limit_reads_as_such(self):
        self.assertIn("charge limit", battery.laptop_text((80, "Not charging")))

    def test_no_battery(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(battery.laptop(root))
        self.assertEqual(battery.laptop_text(None), "no battery found")


class StatusReport(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = f"{d}/zenduo/kb-status"
            battery.save_status(bytes([0x5A, 0x3D, 0x63, 0x00, 0x00, 0x00]), now=1000, path=path)
            data, stamp = battery.load_status(path)
            self.assertEqual((data, stamp), (bytes([0x5A, 0x3D, 0x63, 0x00]), 1000))
            self.assertEqual(battery.status_percent(data), 99)
            battery.clear_status(path)
            self.assertIsNone(battery.load_status(path))

    def test_a_value_over_100_is_not_a_percentage(self):
        self.assertIsNone(battery.status_percent(bytes([0x5A, 0x3D, 0xC8, 0x00])))
        self.assertIsNone(battery.status_percent(bytes([0x5A, 0x4E, 0x01])))

    def test_garbage_file(self):
        with tempfile.TemporaryDirectory() as d:
            write(f"{d}/s", "zz\nnot a time\n")
            self.assertIsNone(battery.load_status(f"{d}/s"))


class KeyboardSources(unittest.TestCase):
    def test_a_hid_battery_node_of_the_keyboard(self):
        with tempfile.TemporaryDirectory() as root:
            write(f"{root}/hid-aa-battery/capacity", "42\n")
            write(f"{root}/hid-aa-battery/device/uevent",
                  "HID_ID=0005:00000B05:00001B2C\nHID_NAME=ASUS Zenbook Duo Keyboard\n")
            write(f"{root}/hid-bb-battery/capacity", "10\n")
            write(f"{root}/hid-bb-battery/device/uevent", "HID_ID=0005:0000046D:0000B023\n")
            self.assertEqual(battery.keyboard_sysfs(root), 42)

    def test_another_device_is_not_the_keyboard(self):
        with tempfile.TemporaryDirectory() as root:
            write(f"{root}/hid-bb-battery/capacity", "10\n")
            write(f"{root}/hid-bb-battery/device/uevent", "HID_ID=0005:0000046D:0000B023\n")
            self.assertIsNone(battery.keyboard_sysfs(root))

    def test_upower_dump(self):
        dump = ("Device: /org/freedesktop/UPower/devices/battery_BAT0\n"
                "  native-path:          BAT0\n  model:                A32N2235\n"
                "  battery\n    percentage:          80%\n\n"
                "Device: /org/freedesktop/UPower/devices/mouse_dev_AA\n"
                "  model:                MX Master\n  mouse\n    percentage:          30%\n\n"
                "Device: /org/freedesktop/UPower/devices/keyboard_dev_BB\n"
                "  model:                ASUS Zenbook Duo Keyboard\n"
                "  keyboard\n    percentage:          64%\n")
        self.assertEqual(battery.parse_upower(dump), 64)
        self.assertIsNone(battery.parse_upower(dump.split("\n\nDevice: /org/freedesktop"
                                                          "/UPower/devices/keyboard")[0]))


class KeyboardText(unittest.TestCase):
    report = (bytes([0x5A, 0x3D, 0x63, 0x00]), 1000)

    def test_unreachable_is_unknown_whatever_was_saved(self):
        text = battery.keyboard_text(False, 50, 60, self.report)
        self.assertTrue(text.startswith("unknown"))

    def test_the_kernel_wins_then_upower_then_the_report(self):
        self.assertEqual(battery.keyboard_text(True, 50, 60, self.report), "50 %")
        self.assertEqual(battery.keyboard_text(True, None, 60, self.report), "60 % (Bluetooth)")
        text = battery.keyboard_text(True, None, None, self.report, now=1000 + 180)
        self.assertTrue(text.startswith("99 %"))
        self.assertIn("3 min ago", text)
        self.assertIn("unconfirmed", text)

    def test_nothing_yet(self):
        self.assertTrue(battery.keyboard_text(True).startswith("unknown"))


if __name__ == "__main__":
    unittest.main()
