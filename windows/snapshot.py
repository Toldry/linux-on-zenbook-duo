r"""Find where MyASUS stores a setting: snapshot, change it, snapshot, diff.

    py snapshot.py take before
    (change one thing in MyASUS)
    py snapshot.py take after
    py snapshot.py diff before after

Read only. A snapshot is the ASUS registry keys plus the ASUS config files
(contents for small text files, a hash for the rest), saved as JSON under
windows/captures/snapshots/. The diff names each value or file that changed,
which tells you the setting's name and its values: what Linux needs to offer
the same choice, even when the hardware side is found elsewhere.

A MyASUS app setting may live in the app's settings.dat, a registry hive the
app keeps locked; the diff can only say that file changed (hash), not what
changed in it. That is a known gap: docs/WINDOWS-RE.md.
"""

import difflib
import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zenduo_re  # noqa: E402

REGISTRY_ROOTS = [
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\ASUS"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\WOW6432Node\ASUS"),
    ("HKEY_CURRENT_USER", r"Software\ASUS"),
    ("HKEY_LOCAL_MACHINE", r"SYSTEM\CurrentControlSet\Services\AsusOptimization"),
    ("HKEY_LOCAL_MACHINE", r"SYSTEM\CurrentControlSet\Services\ASUSSystemAnalysis"),
    ("HKEY_LOCAL_MACHINE", r"SYSTEM\CurrentControlSet\Services\ASUSSwitch"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"),
]
FILE_GLOBS = [
    r"C:\ProgramData\ASUS\**\*",
    r"C:\Program Files\ASUS\**\*.json",
    r"C:\Program Files\ASUS\**\*.xml",
    r"C:\Program Files\ASUS\**\*.ini",
    os.path.expandvars(r"%LOCALAPPDATA%\Packages\B9ECED6F.ASUSPCAssistant_*\LocalState\**\*"),
    os.path.expandvars(r"%LOCALAPPDATA%\Packages\B9ECED6F.ASUSPCAssistant_*\Settings\*"),
    os.path.expandvars(r"%LOCALAPPDATA%\ASUS\**\*"),
]
TEXT_EXTENSIONS = (".json", ".xml", ".ini", ".txt", ".cfg", ".config", ".log", ".csv")
TEXT_LIMIT = 512 * 1024


def walk_registry(hive_name, path):
    """{key\\value: repr(data)} for every value under one key, recursively."""
    import winreg
    hive = getattr(winreg, hive_name)
    values = {}

    def visit(subpath):
        try:
            key = winreg.OpenKey(hive, subpath, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        except OSError:
            return
        with key:
            index = 0
            while True:
                try:
                    name, data, kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                if isinstance(data, bytes):
                    data = data.hex()
                values[f"{hive_name}\\{subpath}\\{name or '(default)'}"] = f"{kind}:{data!r}"
                index += 1
            index = 0
            while True:
                try:
                    child = winreg.EnumKey(key, index)
                except OSError:
                    break
                visit(f"{subpath}\\{child}")
                index += 1

    visit(path)
    return values


def read_files():
    files = {}
    for pattern in FILE_GLOBS:
        for path in glob.glob(pattern, recursive=True):
            if not os.path.isfile(path):
                continue
            entry = {"size": os.path.getsize(path)}
            try:
                with open(path, "rb") as f:
                    data = f.read()
                entry["sha256"] = hashlib.sha256(data).hexdigest()
                if path.lower().endswith(TEXT_EXTENSIONS) and len(data) <= TEXT_LIMIT:
                    entry["text"] = data.decode("utf-8", errors="replace")
            except OSError as error:
                entry["error"] = str(error)      # locked: settings.dat while the app runs
            files[path] = entry
    return files


def diff_snapshots(before, after):
    """Human-readable lines for what changed between two snapshots."""
    lines = []
    for section in ("registry", "files"):
        old, new = before.get(section, {}), after.get(section, {})
        for name in sorted(set(old) | set(new)):
            if name not in old:
                lines.append(f"+ {section}: {name} = {summarise(new[name])}")
            elif name not in new:
                lines.append(f"- {section}: {name}")
            elif old[name] != new[name]:
                if section == "files" and "text" in old[name] and "text" in new[name]:
                    lines.append(f"~ file: {name}")
                    lines.extend("    " + line.rstrip("\n") for line in difflib.unified_diff(
                        old[name]["text"].splitlines(), new[name]["text"].splitlines(),
                        lineterm="", n=1) if not line.startswith(("---", "+++")))
                else:
                    lines.append(f"~ {section}: {name}: {summarise(old[name])}"
                                 f" -> {summarise(new[name])}")
    return lines


def summarise(value):
    if isinstance(value, dict):
        return value.get("sha256", value.get("error", ""))[:16] + f" ({value.get('size')} B)"
    return value


def snapshot_path(name):
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures", "snapshots")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{name}.json")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "take":
        zenduo_re.require_windows("snapshot")
        registry = {}
        for hive, path in REGISTRY_ROOTS:
            registry.update(walk_registry(hive, path))
        snap = {"registry": registry, "files": read_files()}
        with open(snapshot_path(argv[1]), "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=1)
        print(f"{len(registry)} registry values, {len(snap['files'])} files"
              f" -> {snapshot_path(argv[1])}")
        return 0
    if len(argv) == 3 and argv[0] == "diff":
        loaded = []
        for name in argv[1:]:
            path = name if os.path.isfile(name) else snapshot_path(name)
            with open(path, encoding="utf-8") as f:
                loaded.append(json.load(f))
        lines = diff_snapshots(*loaded)
        print("\n".join(lines) or "no difference")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
