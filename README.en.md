# EdifierGuard

> Keeps the audio endpoint of a **HECATE G3 Max (dock version)** (and similar 2.4 GHz headsets)
> visible **only while the headset is actually powered on**.
>
> Headset offline → hide the audio endpoint. Headset online → restore it.
> No administrator rights, silent background, starts at logon.
>
> **One file, nothing else**: [`EdifierGuard.exe`](EdifierGuard.exe) in the repo root is a
> **single self-contained file** — no Python, no dependencies, no install. **Just double-click it.**
> At runtime it creates **no files at all** (no logs, no config, no PIDs, no install folder), so you can copy it
> to any Windows machine and it just works.
>
> The only on-disk artifact is inherent to single-file packaging: the exe unpacks itself into
> `%TEMP%\_MEIxxxxxx` (~16 MB) on start and **deletes it on exit**. If the process is force-killed the
> folder may linger — `--stop` / `--install` / `--uninstall` clean those up.

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
① Detection : poll the receiver over HID (0.5s after a change; 2s stable; up to 5s when online)
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

### Option 1 — single-file exe (recommended: one file does it all)

**`EdifierGuard.exe`** in the repo root is a **self-contained single file**:

- no Python, no `lib\`, no `hidapi.dll`, no external path — everything is packed in;
- **zero files at runtime**: no logs, config, PID or heartbeat files — all coordination
  happens in memory (`ipc.py`). The only exception is packaging-inherent: the exe unpacks
  itself into `%TEMP%\_MEIxxxxxx` (~16 MB) on start and deletes it on exit;
- **double-click = silent background daemon** (no window, no tray, no UAC);
- **runs anywhere**: copy it to any Windows 10/11 machine and it works, with no configuration.

```bat
EdifierGuard.exe --doctor       :: health check (run this first)
EdifierGuard.exe --install      :: autostart: copy the exe into your Startup folder + start it
EdifierGuard.exe --uninstall    :: stop + remove autostart + restore the endpoint to "enabled"
```

> `--install` merely copies **the exe itself** into Startup — no registry keys, no service,
> no scheduled task, no external dependency — and it also **cleans up any legacy autostart
> method** it finds (old `.vbs` launcher, `Run` registry values, old scheduled tasks, old install dir).
> You can also just drop `EdifierGuard.exe` into Startup manually (`Win+R` → `shell:startup`).
> Once it is there, **that one file is everything** — you can delete the project folder.

### Option 2 — run from source (developers)

Requirements: **Windows 10/11**, **Python 3.8+** (or a portable interpreter in `runtime\`).
Runtime deps are bundled (`lib\hid`, `runtime\hidapi.dll`) — no `pip` install.
**No administrator rights.**

```bat
python main.py --doctor       :: health check
python main.py                :: background daemon (watchdog -> guard.py)
python watchdog.py            :: same thing
```

Build the exe yourself:

```bat
python -m pip install pyinstaller
python tools\build_exe.py     :: produces EdifierGuard.exe in the repo root
```

### Layout

```
EdifierGuard/
  EdifierGuard.exe         <- ★ single self-contained file (no Python, no external files,
                              and it creates no files at runtime — beyond the
                              packaging-inherent %TEMP%\_MEIxxxxxx self-unpack folder)
  main.py                  <- exe entry point (dispatch / console handoff / --install)
  guard.py  watchdog.py    <- daemon + supervisor
  ipc.py                   <- in-memory IPC (shared memory + named mutex), no files
  guard_config.json        <- optional template (read-only; never created by the program)
  lib\hid\                 <- bundled hid module (pure ctypes)
  runtime\hidapi.dll       <- bundled HID library (source runs; already inside the exe)
  docs\   tools\   scripts\
```

Runtime data: **none**. Diagnostics can optionally log to a file, but only when you ask:

```bat
set EDIFIERGUARD_LOG=%TEMP%\edifierguard.log
EdifierGuard.exe --debug
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
> When run from source, replace `EdifierGuard.exe` with `python main.py`. Command output goes
> back to your console; if you double-click a one-shot command with no console, the result is
> shown in a **message box** — either way **no file is written**.

Helper scripts (all in `scripts\`): `启动.vbs` (manual start, prefers the Startup copy),
`停止.bat` (stop + restore), `状态.bat` (status panel), `诊断.bat` (health check),
`FIX_AUDIO_DEVICE.bat` (admin, USB audio interface rescue), `CLEAN_OLD_TASK.bat`
(admin, remove a legacy scheduled task). Developer tools (in `tools\`):
`build_exe.py`, `gui_server.py` + `preview.html`, `repair_device.py`, `watch.py`,
`selftest.py`. These scripts prefer the exe and are **path-independent**.

---

## Configuration

**No configuration is needed**: the defaults are built into `guard.py`, and the program
**never creates** a config file. If you really want to tweak things (advanced), drop a
`guard_config.json` **next to the exe** — it is only ever read, never written.
[`guard_config.json`](guard_config.json) in the repo is a ready-made template.

| Key | Default | Meaning |
|---|---|---|
| `mode` | `enforce` | `enforce` acts; `monitor` only observes |
| `poll_interval` | `0.5` | fast poll interval right after a change (s) |
| `poll_interval_online` | `2.0` | base poll interval while **online** & stable (s) |
| `poll_interval_offline` | `5.0` | poll interval while **disconnected / unknown** (s) |
| `idle_after_secs` | `25.0` | how long stable before leaving the fast-poll window |
| `online_backoff` | see below | extra ladder used **only while online & stable**: `[stable_secs, interval_secs]`, largest match wins |
| `use_online_bit` | `false` | also treat the "online bit" as evidence of online |
| `offline_confirm_secs` | `3.0` | how long "offline" must persist before acting |
| `max_apply_failures` | `3` | consecutive failures before the fuse blows |
| `auto_repair` | `true` | auto-repair stuck endpoint states when the device is present |

**Adaptive polling — why only the "online" phase slows down:**

| Headset state | Interval |
|---|---|
| Just changed (first 25s) | `0.5s` |
| **Disconnected / unknown** | `5.0s` |
| **Online**, just stabilised | `2.0s` |
| **Online**, stable for 5 min | `3.0s` |
| **Online**, stable for 15 min | `5.0s` |

**An important boundary (not a bug):** the fast window only speeds up polling *after* a
change has been **detected**. If the change happens at t, the guard is asleep inside the
then-current interval and only learns about it at t+interval — so those intervals set the
worst-case "first detection" delay, and the fast window can't fix that one moment.
(Also, the headset itself keeps its link up for a while after you put it down, and during
that time it still reads as "online".)

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
