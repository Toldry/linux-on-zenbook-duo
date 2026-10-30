r"""Measure what the speakers play, on Windows and on Linux, the same way.

    py -m pip install numpy sounddevice
    py speaker_response.py measure --label windows-default
    python3 speaker_response.py measure --label linux-bypassed     (on Ubuntu)
    python3 speaker_response.py compare windows-default linux-bypassed

The gap this is for: Windows voices the speakers in an APO above the driver
(docs/HARDWARE.md, Speakers) and we do not know its curve. Playing the same
pink noise through each OS's normal output path and recording it with the
same microphone gives two third-octave curves; their difference, at each
level, is what the APO does. lib/speaker_dsp.py can then be fitted to it.

Read the result knowing its limits (docs/WINDOWS-RE.md, Gaps):
  * The internal microphone is coloured and sits behind its own processing.
    On Windows, turn off "Audio enhancements" for the microphone (Settings >
    System > Sound > the microphone) and any AI noise cancelation, or the
    recording is shaped twice. A USB measurement microphone on a stand, in the
    same spot for both systems, is better; --input picks it.
  * The APO is very likely level-dependent (bass boost and a limiter), so
    measure several --level values; a single curve is one slice of it.
  * Same volume setting on both systems is not the same loudness. Compare
    shapes (each curve relative to its own 630 Hz band), not levels.

Playback goes through the shared-mode default output on purpose: exclusive
mode would skip the APO we are trying to measure.
"""

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RATE = 48000
SECONDS = 8.0
BAND_CENTRES = [100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
                2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000]
REFERENCE_BAND = 630            # nix/audio.nix normalises to this band


def pink_noise(numpy, seconds=SECONDS, rate=RATE, level_dbfs=-20.0, seed=1):
    """Pink noise by 1/sqrt(f) shaping of white noise, scaled to an RMS level."""
    count = int(seconds * rate)
    rng = numpy.random.default_rng(seed)
    spectrum = numpy.fft.rfft(rng.standard_normal(count))
    freqs = numpy.fft.rfftfreq(count, 1 / rate)
    freqs[0] = freqs[1]
    signal = numpy.fft.irfft(spectrum / numpy.sqrt(freqs), count)
    signal *= 10 ** (level_dbfs / 20) / numpy.sqrt(numpy.mean(signal ** 2))
    fade = int(0.05 * rate)
    ramp = numpy.linspace(0, 1, fade)
    signal[:fade] *= ramp
    signal[-fade:] *= ramp[::-1]
    return signal.astype("float32")


def band_levels(numpy, recording, rate=RATE):
    """{centre Hz: dB} of energy in each third-octave band (absolute dBFS-ish)."""
    spectrum = numpy.abs(numpy.fft.rfft(recording * numpy.hanning(len(recording)))) ** 2
    freqs = numpy.fft.rfftfreq(len(recording), 1 / rate)
    levels = {}
    for centre in BAND_CENTRES:
        low, high = centre / 2 ** (1 / 6), centre * 2 ** (1 / 6)
        energy = spectrum[(freqs >= low) & (freqs < high)].sum()
        levels[centre] = 10 * math.log10(energy) if energy > 0 else -200.0
    return levels


def relative(levels, reference=REFERENCE_BAND):
    ref = levels[reference] if reference in levels else max(levels.values())
    return {centre: round(level - ref, 1) for centre, level in levels.items()}


def results_dir():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures", "speaker")
    os.makedirs(path, exist_ok=True)
    return path


def measure(args):
    try:
        import numpy
        import sounddevice
    except ImportError:
        sys.exit("speaker_response: needs numpy and sounddevice"
                 " (py -m pip install numpy sounddevice).")
    results = {"label": args.label, "levels": {}}
    print("Quiet room, lid open, nothing else playing. Starting in 3 s.")
    sounddevice.sleep(3000)
    # A silent pass first: the noise floor, so bands at the floor can be flagged.
    floor = sounddevice.rec(int(2 * RATE), samplerate=RATE, channels=1, device=args.input)
    sounddevice.wait()
    floor_levels = band_levels(numpy, floor[:, 0])
    for level in args.level:
        stimulus = pink_noise(numpy, level_dbfs=level)
        stereo = numpy.column_stack([stimulus, stimulus])
        print(f"playing pink noise at {level} dBFS ...")
        recording = sounddevice.playrec(stereo, samplerate=RATE, channels=1,
                                        device=(args.input, args.output))
        sounddevice.wait()
        settled = recording[int(0.5 * RATE):, 0]     # skip the onset (limiters attack)
        absolute = band_levels(numpy, settled)
        results["levels"][str(level)] = {
            "relative_db": relative(absolute),
            "absolute_db": {c: round(v, 1) for c, v in absolute.items()},
            "at_noise_floor": [c for c in BAND_CENTRES if absolute[c] - floor_levels[c] < 6],
        }
        print_curve(f"{args.label} @ {level} dBFS", results["levels"][str(level)])
    path = os.path.join(results_dir(), f"{args.label}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"written {path}")
    return 0


def print_curve(title, entry):
    print(title)
    for centre, value in entry["relative_db"].items():
        flag = "  (noise floor)" if int(centre) in entry.get("at_noise_floor", []) else ""
        print(f"  {int(centre):>6} Hz {value:>7.1f} dB{flag}")


def load(name):
    path = name if os.path.isfile(name) else os.path.join(results_dir(), f"{name}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compare_curves(first, second):
    """{level: {centre: first - second}} for the levels both measured."""
    out = {}
    for level, entry in first["levels"].items():
        other = second["levels"].get(level)
        if not other:
            continue
        out[level] = {int(c): round(v - other["relative_db"][c], 1)
                      for c, v in entry["relative_db"].items() if c in other["relative_db"]}
    return out


def compare(args):
    first, second = load(args.first), load(args.second)
    differences = compare_curves(first, second)
    if not differences:
        print("No level was measured in both files.")
        return 1
    for level, curve in differences.items():
        print(f"{first['label']} minus {second['label']} at {level} dBFS"
              " (each relative to its own 630 Hz band):")
        for centre, value in curve.items():
            print(f"  {centre:>6} Hz {value:>+7.1f} dB")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("measure")
    p.add_argument("--label", required=True, help="name for this run, e.g. windows-music")
    p.add_argument("--level", type=float, action="append",
                   help="stimulus RMS in dBFS, repeatable (default -30 -20 -12)")
    p.add_argument("--input", help="input device name or index (sounddevice)")
    p.add_argument("--output", help="output device name or index (sounddevice)")
    p = sub.add_parser("compare")
    p.add_argument("first")
    p.add_argument("second")
    args = parser.parse_args(argv)
    if args.cmd == "measure":
        args.level = args.level or [-30.0, -20.0, -12.0]
        return measure(args)
    return compare(args)


if __name__ == "__main__":
    sys.exit(main())
