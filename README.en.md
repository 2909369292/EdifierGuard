# EdifierGuard

> Keeps the audio endpoint of a **HECATE G3 Max** (and similar 2.4 GHz headsets)
> visible **only while the headset is actually powered on**.
>
> Headset offline → hide the audio endpoint. Headset online → restore it.
> No administrator rights, silent background, starts at logon.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6)
![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB)

[中文说明 (Chinese)](README.md)

---

## The problem

The HECATE G3 Max uses a **2.4 GHz USB dongle** that is always plugged in. Windows
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
`ACTIVE`** when the headset is off. Built in today: **HECATE G3 Max**
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

Requirements: **Windows 10/11**, **Python 3.8+** (or a portable interpreter in
`runtime\`). Runtime deps are bundled (`lib\hid`, `runtime\hidapi.dll`) — no `pip`
install. **No administrator rights.**

1. Clone or download and unpack this repository.
2. Double-click **`INSTALL.bat`** (same as `python install.py`): writes the logon
   launcher to the Startup folder and starts `watchdog.py` + `guard.py`.
3. Run `python guard.py --doctor` to verify.

Uninstall: delete `EdifierGuard.vbs` from your Startup folder, then
`python guard.py --restore`.

---

## Usage

| Goal | Command |
|---|---|
| Health check | `python guard.py --doctor` |
| Status | `python guard.py --status` |
| List endpoints | `python guard.py --list` |
| Judge once (battery / online bit / peer MAC) | `python guard.py --once` |
| Observe only (no changes) | `python guard.py --monitor` |
| Verbose foreground log | `python guard.py --debug` |
| Restore all endpoints to enabled | `python guard.py --restore` |
| Clear the fuse | `python guard.py --reset-fuse` |
| Self-test (logic only) | `python selftest.py` |
| Self-test with fault injection | `python selftest.py --inject` |

Helper scripts: `INSTALL.bat`, `启动.vbs` (manual start), `停止.bat` (stop + restore),
`测试_状态面板.bat` / `测试_实时日志.bat`, `START_MONITOR.bat`, `FIX_AUDIO_DEVICE.bat`
(admin, USB audio interface rescue), `CLEAN_OLD_TASK.bat` (admin, remove a legacy
scheduled task), plus `gui_server.py` + `preview.html`, `repair_device.py`, `watch.py`.

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
