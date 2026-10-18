# EdifierGuard

> Keeps the audio endpoint of a **HECATE G3 Max (dock version)** (and similar 2.4 GHz headsets)
> visible **only while the headset is actually powered on**.
>
> Headset offline → hide the audio endpoint. Headset online → restore it.
> No administrator rights, silent background, starts at logon.
>
> **Ready to run**: [`EdifierGuard.exe`](EdifierGuard.exe) in the repo root is a
> **single self-contained file** — no Python, no dependencies, no install. **Just double-click it.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6)
![Single file](https://img.shields.io/badge/Delivery-single--file%20exe-2ea44f)
![Python](https://img.shields.io/badge/Python%203.8%2B-source%20only-3776AB)

[中文说明 (Chinese)](README.md)

---

## The problem

The HECATE G3 Max (dock version) uses a **2.4 GHz USB dongle** that is always plugged in. Windows
only sees the dongle, so:

- the "HECATE G3 Max" audio endpoint is **always offered**, even when the headset is
  switched off and sitting on its dock;
- the endpoint stays **`ACTIVE`** (unlike a real unplug, it never becomes `NOTPRESENT`),
  so Windows or apps may route audio into a dead endpoint or flip back and forth.

**EdifierGuard** polls the dongle's vendor HID reports to learn the headset's *real*
state, then:

- headset **offline** → **hide** the endpoint via the undocumented
  `IPolicyConfig::SetEndpointVisibility`;
- headset **online** → **restore** it immediately.

This project only ever **hides / shows endpoints** — it does not switch the default
device. See [docs/design-notes.md](docs/design-notes.md) for the trade-off.

---

## Scope (important)

The framework is generic; the detection layer is device-specific.

### Supported

Any USB/2.4 GHz headset whose **dongle is always present but whose endpoint stays
`ACTIVE`** when the headset is off. Built in today: **HECATE G3 Max (dock version)**
(`USB\VID_35BB&PID_A22B`, a **Jieli** dongle, `op=0x0A` status frame).

### Porting to another headset

Add a *device profile* — no framework change needed: (1) VID/PID + HID collection,
(2) the vendor query frame, (3) how to parse online/offline, (4) how to match the
`MMDevices` endpoints.

### Not needed for

- **Bluetooth** headsets — Windows removes/断开s the endpoint on disconnect.
- Headsets whose endpoint flips to `UNPLUGGED`/`NOTPRESENT` by itself (e.g. some Jabra).
- Wired headsets — unplugging removes them.

---

## How it works

```
① Detection : poll the receiver over HID (0.5s; 1.2s when stable)
              send query frame, parse op=0x0A
              battery>0 -> online ; battery==0 / short frame -> offline ; no data -> unknown
② Decision  : asymmetric debounce (online = instant, offline = confirm for 3s)
③ Action    : IPolicyConfig::SetEndpointVisibility, only on this headset's endpoints
④ Safety    : re-verify with Windows audio APIs, roll back to "enabled" on mismatch,
              fuse (observe-only) after repeated failures
```

Design rules: an empty endpoint set is a **failure** (never a fake success); the
program **never trusts its own return value**; failures roll back to the **safe**
(visible) state; `unknown` never changes anything.

---

## Features

- No admin rights (uses the same interface Windows Settings uses), no UAC.
- Watchdog restarts `guard.py` on exit **or hang** (heartbeat).
- Independent post-action verification + automatic rollback.
- Easy to reason about: `--doctor`, `--status`, `--list`, `--once`, `--monitor`, `--debug`.
- Low footprint: ~0.15% of one core idle, ~40 MB RAM.

---

## Install

### Option 1 — single-file exe (recommended: no Python, no install, no external paths)

**`EdifierGuard.exe`** in the repo root is a **self-contained single file**:

- no Python, no `lib\`, no `hidapi.dll`, no external path — everything is packed in;
- **double-click = silent background daemon** (no window, no tray, no UAC);
- runtime data (logs/config) always lives in `%LOCALAPPDATA%\EdifierGuard\`, so the exe can
  sit **anywhere** (repo root, Desktop, USB stick) — **moving the project changes nothing**.

```bat
EdifierGuard.exe --doctor       :: health check (run this first)
EdifierGuard.exe --install      :: autostart: copy the exe into your Startup folder + start it
EdifierGuard.exe --uninstall    :: stop + remove autostart + restore the endpoint to "enabled"
```

> `--install` merely copies **the exe itself** into Startup — no registry keys, no external
> dependency. You can also just drop `EdifierGuard.exe` into Startup manually.
> Override the data directory with the `EDIFIERGUARD_HOME` environment variable (advanced).

### Option 2 — run from source (developers)

Requirements: **Windows 10/11**, **Python 3.8+** (or a portable interpreter in `runtime\`).
Runtime deps are bundled (`lib\hid`, `runtime\hidapi.dll`) — no `pip` install.
**No administrator rights.**

```bat
python watchdog.py            :: background daemon (watchdog -> guard.py)
python guard.py --doctor      :: health check
```

Build the exe yourself:

```bat
python -m pip install pyinstaller
python tools\build_exe.py     :: produces EdifierGuard.exe in the repo root
```

### Layout

```
EdifierGuard/
  EdifierGuard.exe         <- ★ single self-contained file (no Python, no external files)
  main.py                  <- exe entry point (dispatch / console handoff / --install)
  guard.py  watchdog.py  guard_config.json
  lib\hid\                 <- bundled hid module (pure ctypes)
  runtime\hidapi.dll       <- bundled HID library (source runs; already inside the exe)
  docs\   tools\   scripts\

%LOCALAPPDATA%\EdifierGuard\   <- fixed data dir (logs / config / PIDs), wherever the exe lives
```

---

## Usage

| Goal | Command |
|---|---|
| Health check | `EdifierGuard.exe --doctor` |
| Status | `EdifierGuard.exe --status` |
| List endpoints | `EdifierGuard.exe --list` |
| Judge once (battery / online bit / peer MAC) | `EdifierGuard.exe --once` |
| Observe only (no changes) | `EdifierGuard.exe --monitor` |
| Verbose foreground log | `EdifierGuard.exe --debug` |
| Restore all endpoints to enabled | `EdifierGuard.exe --restore` |
| Clear the fuse | `EdifierGuard.exe --reset-fuse` |
| Stop the daemon (and restore endpoints) | `EdifierGuard.exe --stop` |
| Install / remove autostart | `EdifierGuard.exe --install` / `--uninstall` |
| Version / help | `EdifierGuard.exe --version` / `--help` |

> Running with **no arguments** = silent background daemon (that is what double-click does).
> When run from source, replace `EdifierGuard.exe` with `python guard.py`
> (`--install` / `--uninstall` / `--stop` are exe-only). Command output is also saved to
> `%LOCALAPPDATA%\EdifierGuard\last-output.txt`.

Helper scripts (all in `scripts\`): `启动.vbs` (manual start, prefers the exe), `停止.bat`
(stop + restore), `测试_状态面板.bat` / `测试_实时日志.bat`, `START_MONITOR.bat`,
`FIX_AUDIO_DEVICE.bat` (admin, USB audio interface rescue), `CLEAN_OLD_TASK.bat`
(admin, remove a legacy scheduled task). Developer tools (in `tools\`):
`build_exe.py`, `gui_server.py` + `preview.html`, `repair_device.py`, `watch.py`,
`selftest.py`. These scripts prefer the exe and are **path-independent**.

---

## Configuration

`guard_config.json`:

| Key | Default | Meaning |
|---|---|---|
| `mode` | `enforce` | `enforce` acts; `monitor` only observes |
| `poll_interval` | `0.5` | fast poll interval (s) |
| `poll_interval_idle` | `1.2` | slow poll interval once stable (s) |
| `idle_after_secs` | `25.0` | how long stable before slowing down |
| `use_online_bit` | `false` | also treat the "online bit" as evidence of online |
| `offline_confirm_secs` | `3.0` | how long "offline" must persist before acting |
| `max_apply_failures` | `3` | consecutive failures before the fuse blows |
| `auto_repair` | `true` | auto-repair stuck endpoint states when the device is present |

---

## Safety & known limits

- `SetEndpointVisibility` is **undocumented** and writes a **system-wide (HKLM)**
  state — it affects every user on the machine. The program only ever toggles this
  headset's own endpoints.
- Layers of protection: empty-set = failure, post-action verification, rollback to
  visible, fuse, startup self-heal, and a monitor-only mode.
- **Firmware/driver updates** may change the HID protocol or endpoint names; the
  parser/matcher would then need updating. This is **unofficial** software, not
  affiliated with Edifier/HECATE.

---

## License & third-party

MIT — see [LICENSE](LICENSE). Bundled third-party components are tri-licensed and
used here under their permissive terms: [`cython-hidapi`](https://github.com/trezor/cython-hidapi)
(`lib/hid/`) and [`hidapi`](https://github.com/libusb/hidapi) (`runtime/hidapi.dll`).
See [docs/THIRD_PARTY_LICENSES.md](docs/THIRD_PARTY_LICENSES.md).

## Related projects

`saad780/blackshark-audio-switcher`, `Ayerdi/PROX2-AutoSwitch`, `Sapd/HeadsetControl`,
`HeyOkay/HaloBattery`, `Belphemur/SoundSwitch` — see the Chinese README for details.
