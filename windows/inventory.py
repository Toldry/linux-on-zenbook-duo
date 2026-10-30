#!/usr/bin/env python3
r"""Write down every piece of ASUS software and hardware Windows knows about.

    py inventory.py                  -> windows/captures/<stamp>-inventory/

Read only. Run it once, as Administrator, before anything else: the other
tools' findings mean little without the versions they were made against, and
the file list (with SHA-256) is what to feed a disassembler (Ghidra, or REA:
docs/WINDOWS-RE.md) when a capture leaves a question open.

Each section is one PowerShell query, saved as JSON; a query that fails is
saved with its error and the rest go on.
"""

import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zenduo_re  # noqa: E402

ASUS = "asus|atk|armoury|glidex|screenxpert|myasus|b9eced6f"

# name -> PowerShell producing objects; each is piped to ConvertTo-Json.
QUERIES = {
    "system": "Get-CimInstance Win32_ComputerSystem | Select Manufacturer,Model,SystemSKUNumber;"
              " Get-CimInstance Win32_BIOS | Select SMBIOSBIOSVersion,ReleaseDate,Version;"
              " Get-CimInstance Win32_OperatingSystem | Select Caption,Version,BuildNumber",
    "services": "Get-CimInstance Win32_Service | Where-Object { ($_.Name + $_.DisplayName +"
                f" $_.PathName) -match '{ASUS}' }} | Select Name,DisplayName,State,StartMode,"
                "StartName,PathName,ProcessId",
    "drivers": "Get-CimInstance Win32_SystemDriver | Where-Object { ($_.Name + $_.DisplayName +"
               f" $_.PathName) -match '{ASUS}' }} | Select Name,DisplayName,State,StartMode,PathName",
    "pnp_devices": "Get-CimInstance Win32_PnPEntity | Where-Object { ($_.DeviceID +"
                   f" $_.Manufacturer + $_.Name) -match 'VID_0B05|VEN_0B05|{ASUS}' }} |"
                   " Select Name,Manufacturer,DeviceID,HardwareID,CompatibleID,Service,"
                   "PNPClass,Status",
    "signed_drivers": "Get-CimInstance Win32_PnPSignedDriver | Where-Object {"
                      f" ($_.Manufacturer + $_.DeviceName + $_.InfName) -match '{ASUS}' }} |"
                      " Select DeviceName,Manufacturer,DriverVersion,DriverDate,InfName,DeviceID",
    "appx": "Get-AppxPackage -AllUsers | Where-Object { ($_.Name + $_.Publisher) -match"
            f" '{ASUS}' }} | Select Name,Version,PackageFullName,InstallLocation",
    "processes": "Get-CimInstance Win32_Process | Where-Object { ($_.Name + $_.ExecutablePath)"
                 f" -match '{ASUS}' }} | Select ProcessId,Name,ExecutablePath,CommandLine",
    "scheduled_tasks": "Get-ScheduledTask | Where-Object { ($_.TaskPath + $_.TaskName +"
                       f" $_.Author) -match '{ASUS}' }} | Select TaskPath,TaskName,State,"
                       "@{n='Actions';e={($_.Actions | ForEach-Object { $_.Execute + ' ' +"
                       " $_.Arguments }) -join '; '}}",
    "wmi_classes": "Get-CimClass -Namespace root/wmi | Where-Object { $_.CimClassName -match"
                   " 'asus|atk|wmnb' } | Select CimClassName,@{n='Methods';e={"
                   "($_.CimClassMethods | ForEach-Object { $_.Name + '(' + (($_.Parameters |"
                   " ForEach-Object { $_.Name + ':' + $_.CimType }) -join ', ') + ')' })"
                   " -join '; '}}",
    "audio_effects": "Get-ChildItem 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\"
                     "MMDevices\\Audio\\Render' | ForEach-Object { $p = $_.PSPath;"
                     " $name = (Get-ItemProperty \"$p\\Properties\" -ErrorAction"
                     " SilentlyContinue).'{a45c254e-df1c-4efd-8020-67d146a850e0},2';"
                     " $fx = Get-ItemProperty \"$p\\FxProperties\" -ErrorAction SilentlyContinue;"
                     " [pscustomobject]@{Endpoint=$_.PSChildName; Name=$name;"
                     " Fx=($fx | Out-String)} }",
    "apo_clsids": "Get-ChildItem 'HKLM:\\SOFTWARE\\Classes\\AudioEngine\\AudioProcessingObjects'"
                  " | ForEach-Object { Get-ItemProperty $_.PSPath } | Select PSChildName,"
                  "FriendlyName,Copyright",
}

SEARCH_ROOTS = [
    r"C:\Program Files\ASUS",
    r"C:\Program Files (x86)\ASUS",
    r"C:\ProgramData\ASUS",
    r"C:\Windows\System32\drivers",
    r"C:\Windows\System32\DriverStore\FileRepository",
]
BINARY_EXTENSIONS = (".exe", ".dll", ".sys", ".inf")


def powershell(script):
    command = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
               f"$ErrorActionPreference='Stop'; @({script}) | ConvertTo-Json -Depth 4"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"error": str(error)}
    if result.returncode:
        return {"error": result.stderr.strip()}
    try:
        return json.loads(result.stdout or "[]")
    except ValueError:
        return {"raw": result.stdout}


def asus_files(roots=SEARCH_ROOTS, pattern=ASUS):
    """Binaries under the roots whose path says ASUS, with size and SHA-256."""
    import re
    regex = re.compile(pattern, re.IGNORECASE)
    found = []
    for root in roots:
        for folder, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(folder, name)
                if not name.lower().endswith(BINARY_EXTENSIONS) or not regex.search(path):
                    continue
                try:
                    with open(path, "rb") as f:
                        digest = hashlib.sha256(f.read()).hexdigest()
                    found.append({"path": path, "size": os.path.getsize(path),
                                  "sha256": digest})
                except OSError as error:
                    found.append({"path": path, "error": str(error)})
    return found


def main(argv=None):
    zenduo_re.require_windows("inventory")
    out = zenduo_re.session_dir("inventory", argv[0] if argv else None)
    for name, script in QUERIES.items():
        print(f"{name} ...", flush=True)
        with open(os.path.join(out, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(powershell(script), f, indent=2)
    print("files ...", flush=True)
    with open(os.path.join(out, "files.json"), "w", encoding="utf-8") as f:
        json.dump(asus_files(), f, indent=2)
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
