"""Text from devices, made safe to print to a terminal."""

# A device names itself: a Bluetooth peer, a USB HID, a monitor's EDID. A name
# carrying escape sequences would reach the terminal as commands, so control
# characters (C0, DEL and C1) become "?". A tab or a newline would also break
# the tab-separated lines `duo doctor` reads.
UNPRINTABLE = dict.fromkeys([*range(0x20), 0x7F, *range(0x80, 0xA0)], "?")


def printable(value):
    """`value` with every string in it made safe to print."""
    if isinstance(value, str):
        return value.translate(UNPRINTABLE)
    if isinstance(value, list):
        return [printable(v) for v in value]
    if isinstance(value, dict):
        return {k: printable(v) for k, v in value.items()}
    return value
