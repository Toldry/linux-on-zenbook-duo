#!/usr/bin/env python3
r"""Log what the ASUS software asks the hardware to do, from inside it.

    py -m pip install frida
    py hook_asus.py --list                   which processes would be hooked
    py hook_asus.py --scenario power         hook them and walk the scenario
    py hook_asus.py --pid 1234 --all-ioctls  one process, every DeviceIoControl

Run from an Administrator terminal: the ASUS services run as SYSTEM.

This attaches Frida to every running process whose name matches --match (ASUS
services and apps by default), re-scans every few seconds for new ones, and
hooks, in user space:

  * CreateFileW: which device paths the process opens (\\.\ATKACPI, HID
    collections), so later calls can be named;
  * DeviceIoControl: the ATKACPI calls (DSTS/DEVS into the BIOS: fan profile,
    charge limit, ...) and HID class IOCTLs, with both buffers;
  * hid.dll HidD_SetFeature / HidD_GetFeature / HidD_SetOutputReport /
    HidD_GetInputReport: keyboard commands, whatever the transport. This is
    the one view that covers Bluetooth without an air sniffer.

Each call is written to hooks.jsonl with the marker that was current, and the
ATK calls are decoded on the spot (zenduo_re.decode_ioctl).

What it cannot see (docs/WINDOWS-RE.md, "Gaps"): anything done in a kernel
driver; WMI method calls (they reach the BIOS through the WMI service, not
through these processes' DeviceIoControl: use wmi_trace.py); the output of an
overlapped DeviceIoControl, which completes after the hook returns; handles
opened before the hook attached (calls on them are logged, the path is
"unknown" unless HidD_GetAttributes answers for it). A process that Windows
protects (PPL) cannot be attached to at all; the tool says so and goes on.
"""

import argparse
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zenduo_re  # noqa: E402

DEFAULT_MATCH = r"asus|armoury|atkex|glidex|screenxpert|myasus"

# The script runs inside the target. Frida 17 dropped the static
# Module.getExportByName(module, name); the helper tries the 17 form first
# and falls back to the 16 one.
AGENT_JS = r"""
'use strict';
const ALL_IOCTLS = %(all_ioctls)s;
const ATK_CODES = [0x0022240C, 0x00222400, 0x00222408];
const MAX_BYTES = 512;
const handles = {};
const inHidD = {};

function exp(mod, name) {
  try { return Process.getModuleByName(mod).getExportByName(name); } catch (e) {}
  try { return Module.getExportByName(mod, name); } catch (e) {}
  return null;
}
function hex(ptr, len) {
  if (ptr.isNull() || len <= 0) return '';
  const n = Math.min(len, MAX_BYTES);
  const bytes = new Uint8Array(ptr.readByteArray(n));
  let s = '';
  for (const b of bytes) s += ('0' + b.toString(16)).slice(-2);
  return s;
}
function key(h) { return h.toString(); }

let getAttributes = null;
function hidIdentity(h) {
  if (handles[key(h)] !== undefined) return handles[key(h)];
  if (getAttributes === null) {
    const p = exp('hid.dll', 'HidD_GetAttributes');
    getAttributes = p ? new NativeFunction(p, 'uchar', ['pointer', 'pointer']) : false;
  }
  if (!getAttributes) return 'unknown';
  const attrs = Memory.alloc(12);
  attrs.writeU32(12);
  if (getAttributes(h, attrs)) {
    const vid = attrs.add(4).readU16(), pid = attrs.add(6).readU16();
    handles[key(h)] = 'hid vid=' + vid.toString(16) + ' pid=' + pid.toString(16);
  } else {
    handles[key(h)] = 'unknown';
  }
  return handles[key(h)];
}

const createFile = exp('kernelbase.dll', 'CreateFileW');
if (createFile) Interceptor.attach(createFile, {
  onEnter(args) { this.path = args[0].isNull() ? '' : args[0].readUtf16String(); },
  onLeave(ret) {
    const p = (this.path || '').toLowerCase();
    if (ret.toInt32() === -1) return;
    if (p.startsWith('\\\\.\\') || p.startsWith('\\\\?\\hid') || p.indexOf('atkacpi') >= 0) {
      handles[key(ret)] = this.path;
      send({api: 'CreateFileW', path: this.path, handle: key(ret)});
    }
  }
});

const ioctl = exp('kernelbase.dll', 'DeviceIoControl');
if (ioctl) Interceptor.attach(ioctl, {
  onEnter(args) {
    this.code = args[1].toUInt32();
    const hidClass = (this.code >>> 16) === 0x000B;
    this.wanted = !inHidD[Process.getCurrentThreadId()] &&
      (ALL_IOCTLS || ATK_CODES.indexOf(this.code) >= 0 || hidClass);
    if (!this.wanted) return;
    this.handle = args[0];
    this.inHex = hex(args[2], args[3].toUInt32());
    this.outPtr = args[4];
    this.outLen = args[5].toUInt32();
    this.returned = args[6];
    this.outBefore = hex(args[4], Math.min(this.outLen, 64));
    this.overlapped = !args[7].isNull();
  },
  onLeave(ret) {
    if (!this.wanted) return;
    let outLen = Math.min(this.outLen, 64);
    if (!this.returned.isNull()) outLen = Math.min(this.returned.readU32(), MAX_BYTES);
    send({api: 'DeviceIoControl', handle: key(this.handle),
          path: handles[key(this.handle)] ||
                ((this.code >>> 16) === 0x000B ? hidIdentity(this.handle) : 'unknown'),
          code: this.code, ok: ret.toInt32() !== 0, overlapped: this.overlapped,
          in: this.inHex, out_before: this.outBefore,
          out: ret.toInt32() !== 0 ? hex(this.outPtr, outLen) : ''});
  }
});

for (const name of ['HidD_SetFeature', 'HidD_GetFeature',
                    'HidD_SetOutputReport', 'HidD_GetInputReport']) {
  const p = exp('hid.dll', name);
  if (!p) continue;
  Interceptor.attach(p, {
    onEnter(args) {
      this.tid = Process.getCurrentThreadId();
      inHidD[this.tid] = true;
      this.handle = args[0]; this.buf = args[1]; this.len = args[2].toUInt32();
      this.before = hex(this.buf, this.len);
    },
    onLeave(ret) {
      inHidD[this.tid] = false;
      send({api: name, handle: key(this.handle), path: hidIdentity(this.handle),
            ok: ret.toInt32() !== 0, in: this.before,
            out: name.indexOf('Get') >= 0 ? hex(this.buf, this.len) : ''});
    }
  });
}
send({api: 'ready'});
"""


def target_processes(device, pattern, own_pid):
    regex = re.compile(pattern, re.IGNORECASE)
    return [p for p in device.enumerate_processes()
            if regex.search(p.name) and p.pid != own_pid]


def enrich(payload):
    """Add the decoded meaning to one message from the agent."""
    if payload.get("api") == "DeviceIoControl":
        decoded = zenduo_re.decode_ioctl(payload["code"],
                                         bytes.fromhex(payload.get("in", "")),
                                         bytes.fromhex(payload.get("out", "")))
        payload["decoded"] = decoded
        linux = zenduo_re.linux_equivalent_atk(decoded) if decoded["kind"] == "atk" else None
        if linux:
            payload["linux"] = linux
    elif payload.get("api", "").startswith("HidD_"):
        data = payload.get("in") if "Set" in payload["api"] else payload.get("out")
        if data and "Feature" in payload["api"]:
            payload["meaning"] = zenduo_re.describe_feature(bytes.fromhex(data))
    return payload


def summary_line(payload):
    api = payload.get("api")
    if api == "DeviceIoControl":
        d = payload["decoded"]
        if d["kind"] == "atk":
            return (f"ATK {d.get('method')} {d.get('device_id', '')} {d.get('device', '')}"
                    f" value={d.get('value', '-')} -> {d.get('returned', '?')}")
        if d["kind"] == "atk-event":
            return f"ATK event {d.get('event')}"
        return f"{d.get('ioctl')} {payload.get('in') or payload.get('out')} [{payload['path']}]"
    if api and api.startswith("HidD_"):
        data = payload.get("in") if "Set" in api else payload.get("out")
        return f"{api} {data} [{payload['path']}] {payload.get('meaning', '')}"
    if api == "CreateFileW":
        return f"open {payload['path']}"
    return api or ""


def main(argv=None):
    zenduo_re.require_windows("hook_asus")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--match", default=DEFAULT_MATCH,
                        help=f"process name regex (default {DEFAULT_MATCH!r})")
    parser.add_argument("--pid", type=int, action="append", help="hook only these PIDs")
    parser.add_argument("--list", action="store_true", help="list matches and exit")
    parser.add_argument("--all-ioctls", action="store_true",
                        help="log every DeviceIoControl, not just ATK and HID (noisy)")
    parser.add_argument("--scenario", choices=sorted(zenduo_re.SCENARIOS))
    parser.add_argument("--rescan", type=float, default=5.0, help="seconds between scans")
    parser.add_argument("--out", help="parent folder for the session (default windows/captures)")
    args = parser.parse_args(argv)
    try:
        import frida
    except ImportError:
        sys.exit("hook_asus: needs Frida. Run: py -m pip install frida")

    device = frida.get_local_device()
    own_pid = os.getpid()
    if args.list:
        for p in target_processes(device, args.match, own_pid):
            print(f"{p.pid:>7}  {p.name}")
        return 0

    out = zenduo_re.session_dir("hooks", args.out)
    log = zenduo_re.JsonLog(os.path.join(out, "hooks.jsonl"))
    markers = zenduo_re.JsonLog(os.path.join(out, "markers.jsonl"))
    source = AGENT_JS % {"all_ioctls": "true" if args.all_ioctls else "false"}
    sessions = {}
    lock = threading.Lock()
    stop = threading.Event()

    def on_message(pid, name):
        def handler(message, _data):
            if message.get("type") != "send":
                log.write({"type": "agent-error", "pid": pid, "process": name,
                           "error": message.get("description", str(message))})
                return
            payload = enrich(dict(message["payload"]))
            payload.update({"type": "call", "pid": pid, "process": name})
            log.write(payload)
            if payload.get("api") not in ("ready",):
                print(f"  {time.strftime('%H:%M:%S')} {name}: {summary_line(payload)}")
        return handler

    def attach(pid, name):
        try:
            session = device.attach(pid)
            script = session.create_script(source)
            script.on("message", on_message(pid, name))
            script.load()
            sessions[pid] = session
            print(f"hooked {name} ({pid})")
            log.write({"type": "attached", "pid": pid, "process": name})
        except Exception as error:      # frida raises a family of its own
            print(f"could not hook {name} ({pid}): {error}")
            log.write({"type": "attach-failed", "pid": pid, "process": name,
                       "error": str(error)})
            sessions[pid] = None        # do not retry every scan

    def scan_forever():
        while not stop.is_set():
            with lock:
                if args.pid:
                    wanted = [(pid, f"pid{pid}") for pid in args.pid]
                else:
                    wanted = [(p.pid, p.name)
                              for p in target_processes(device, args.match, own_pid)]
                for pid, name in wanted:
                    if pid not in sessions:
                        attach(pid, name)
            stop.wait(args.rescan)

    scanner = threading.Thread(target=scan_forever, daemon=True)
    scanner.start()
    time.sleep(1.0)
    if not any(sessions.values()):
        print("Nothing hooked yet. Is this an Administrator terminal, and is MyASUS"
              " installed? Scanning continues.")
    zenduo_re.run_markers(markers, args.scenario)
    stop.set()
    with lock:
        for session in sessions.values():
            if session is not None:
                try:
                    session.detach()
                except Exception:
                    pass
    log.close()
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
