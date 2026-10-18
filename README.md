# EdifierGuard

> 让 **HECATE G3 Max（底座版）** 这类「接收器常驻、耳机可关机」的无线耳机，**只在耳机真正在线时出现在声音设置里**。
>
> 耳机离线 → 自动隐藏音频端点；耳机上线 → 自动恢复。全程**免管理员**、静默后台、开机自启。
>
> **就一个文件**：根目录的 [`EdifierGuard.exe`](EdifierGuard.exe) 是**单文件自包含**程序 —— 免装 Python、免装依赖、免安装，**双击就跑**。
> 运行期**不产生任何文件**（没有日志、没有配置、没有 pid、没有安装目录），
> 复制到任何一台 Windows 电脑都能直接用。
>
> 唯一的落盘是单文件打包本身固有的：exe 启动时会把自身解包到 `%TEMP%\_MEIxxxxxx`（约 16 MB），
> **程序退出时自动删除**；被强杀时可能残留，`--stop` / `--install` / `--uninstall` 会顺手清掉。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6)
![Single file](https://img.shields.io/badge/Delivery-single--file%20exe-2ea44f)
![Python](https://img.shields.io/badge/Python%203.8%2B-source%20only-3776AB)

[English README](README.en.md)

---

## 目录

- [它解决什么问题](#它解决什么问题)
- [适用范围（重要）](#适用范围重要)
- [工作原理](#工作原理)
- [特性](#特性)
- [安装](#安装)
- [使用](#使用)
- [配置](#配置)
- [安全设计与已知边界](#安全设计与已知边界)
- [目录结构](#目录结构)
- [常见问题](#常见问题)
- [免责声明](#免责声明)
- [许可证与第三方](#许可证与第三方)
- [相关项目](#相关项目)

---

## 它解决什么问题

HECATE G3 Max **底座版**用的是 **2.4G USB 接收器（dongle）**，而 Windows 只认那个接收器：

- 接收器**一直插着**，所以 Windows 永远认为「HECATE G3 Max」这个音频设备**可用**；
- 即使耳机已经关机、放进充电底座，音频端点依然是 **`ACTIVE`**（不会像 USB 拔插那样变成 `NOTPRESENT`）；
- 于是：声音设置里一直挂着它、Windows 或应用可能把声音送到这个「死端点」、或发生来回自动切换。

**EdifierGuard 的做法**：主动向接收器查询厂商 HID 报文，判断耳机**真实的在线/离线状态**；然后：

- 耳机**离线** → 用未公开的 `IPolicyConfig::SetEndpointVisibility` 把音频端点**隐藏**（从活跃列表移除）；
- 耳机**上线** → 立刻**恢复**为可用。

> 本项目只做「隐藏 / 显示端点」这一件事（不切换默认设备）。关于两种方案的取舍，见 [docs/design-notes.md](docs/design-notes.md)。

---

## 适用范围（重要）

这个项目是**框架通用、设备专属**的：

### ✅ 适用（以及为什么需要它）

**任何「USB/2.4G 接收器常驻 + 耳机可关机，且音频端点在耳机离线时仍保持 `ACTIVE`」的无线耳机**，理论上都适用——因为 Windows 自己不会把端点标成「未插入」，只能靠外部守护。

- **当前内置支持**：**HECATE G3 Max 底座版**（`USB\VID_35BB&PID_A22B`，接收器为 **杰理 Jieli** 芯片，协议为 `op=0x0A` 状态帧）。
- ⚠️ **不是这个型号**：`USB\VID_35BB&PID_A22A`「HECATE G3 MAX GAMING HEADSET」是**耳机 USB 直连**（不经过接收器底座），拔掉即从系统消失，**不需要**本程序。

### 🔧 想支持其它耳机需要做什么

只需新增一个「设备档案」，框架不用动：

1. **VID/PID 与 HID 集合**：找到能收发厂商报文的那个集合（本机是 `MI_03` 的 `Col03`）；
2. **查询帧**：厂商驱动用来询问状态的报文；
3. **状态解析**：从回包里取「在线/离线」（本机是电量字节 + 在线标志位）；
4. **端点匹配规则**：怎么从 `MMDevices` 里认出这款耳机的端点。

> 换句话说：**检测层是设备专属的，动作层（隐藏/显示端点 + 多重校验 + 熔断回滚）是通用的。**

### ❌ 不需要本工具的耳机

| 情况 | 为什么不需要 |
|---|---|
| **蓝牙耳机** | Windows 通常在断开时就会移除/断开对应端点 |
| **端点会随耳机离线自动变 `UNPLUGGED`/`NOTPRESENT` 的耳机**（如某些 Jabra） | Windows 已经能正确反映状态，事件驱动即可 |
| **有线耳机** | 拔掉即消失，无需干预 |

---

## 工作原理

```
┌──────────────────────────────────────────────────────────────┐
│  ① 检测层   HID 轮询接收器（默认 0.5s；判定稳定 25s 后转 1.2s） │
│     发送查询帧 D0 35 BB 01… ，解析 op=0x0A 回包                  │
│       ├─ 电量 > 0            → online                          │
│       ├─ 电量 == 0           → offline                         │
│       ├─ 简帧（无电量字节）    → offline                         │
│       └─ 读不到（丢包）        → unknown（保持现状）              │
├──────────────────────────────────────────────────────────────┤
│  ② 判定层   非对称防抖                                          │
│     上线：立即生效          离线：需持续 3s 确认（防接触抖动）    │
├──────────────────────────────────────────────────────────────┤
│  ③ 动作层   IPolicyConfig::SetEndpointVisibility               │
│     只操作 HECATE 自己的 2 个端点，绝不碰其它设备                │
│     隐藏前还会「当场再读一次电量 + 在线位」作最后一道闸           │
├──────────────────────────────────────────────────────────────┤
│  ④ 安全层   操作后用 Windows 音频 API 独立校验                   │
│     校验不过 → 立即回滚成「可用」（宁可多显示，不可让它消失）     │
│     连续失败 3 次 → 熔断（内存标记），转只观察，不再碰设备        │
└──────────────────────────────────────────────────────────────┘
```

**关键设计取舍：**

- **空集合 = 失败**：绝不「假成功」（这是 v1 把设备永久屏蔽的根因）。
- **不信自己的返回值**：每次操作后都用 `IMMDeviceEnumerator` 活跃列表 + `IAudioEndpointVolume` 独立复核。
- **失败向安全方向回滚**：校验不过就恢复为「可用」，而不是继续隐藏。
- **未知不动**：读不到电量时不改变设备状态。

---

## 特性

- 🚀 **免管理员**：`SetEndpointVisibility` 普通用户权限即可调用；安装/运行全程无 UAC。
- 🛡 **看门狗守护**：`guard.py` 退出**或卡死**（心跳停止）都会被 `watchdog.py` 自动重启。
- ✅ **操作后独立校验**：不达标就回滚，杜绝「假成功」。
- ⏱ **非对称防抖**：上线立刻响应（约 0.3s），离线需持续确认（默认 3s）。
- 🔌 **故障隔离**：底层设备被禁用（PnP 问题码 22）时**完全不动端点**，只记录日志。
- 🧯 **自动熔断**：连续失败后转「只观察」模式。
- 🔄 **启动自愈**：开机时若端点卡在隐藏态，先无条件恢复为可用，再由判定决定去留。
- 🪶 **低占用**：空闲约 **0.15% 单核 CPU**、约 **40MB 内存**（含看门狗）。
- 🔍 **可观测**：`--doctor` 一句话体检、`--status` / `--list` / `--once` 一应俱全；需要时一条环境变量即可开日志。

---

## 安装

### 方式一：单文件 exe（推荐 —— 一个文件搞定）

仓库根目录的 **`EdifierGuard.exe`** 是**自包含**的单文件程序：

- **不需要**安装 Python，**不需要** `lib/`、`hidapi.dll` 或任何外部路径 —— 全部打进这一个 exe；
- **运行期零文件**：不生成日志、配置、pid、心跳等任何文件，所有状态都在内存里（见 `ipc.py`）；
  唯一例外是**打包方式本身**决定的临时解包目录 `%TEMP%\_MEIxxxxxx`（约 16 MB），
  由 exe 启动时创建、**退出时自动删除**，不属于本程序写入的数据；
- **双击即用**：双击后**静默**后台常驻（无窗口、无托盘、无 UAC）；
- **到处能跑**：复制到任何一台 Windows 10/11 电脑都直接可用，不需要任何配置。

**步骤 1 · 先体检：**

```bat
EdifierGuard.exe --doctor
```

**步骤 2 · 装到「启动」文件夹（开机自启）：**

```bat
EdifierGuard.exe --install
```

`--install` 会把**这个 exe 本身**复制一份进「启动」文件夹并立即启动；
不写注册表、不建服务、不建计划任务、不留任何外部依赖，
**同时自动清理历史上出现过的其它自启方式**（旧 `.vbs` 启动器、注册表 `Run` 项、旧计划任务、旧安装目录）。

> 也可以手动把 `EdifierGuard.exe` 直接拖进「启动」文件夹（`Win+R` → 输入 `shell:startup`），效果完全一样。
> 放进「启动」文件夹之后，**这一个文件就是全部**：项目文件夹删掉也照常自启、照常工作。

**卸载：**

```bat
EdifierGuard.exe --uninstall     :: 停止 + 移除自启 + 把端点恢复为「可用」
```

### 方式二：源码运行（开发者）

- **Windows 10 / 11**；
- **Python 3.8+**（任意发行版），或把便携解释器放到 `runtime\python.exe`；
- 运行时依赖已**内置**（`lib\hid` + `hidapi.dll`），无需 `pip install`；
- **不需要管理员权限**。

```bat
python main.py --doctor       :: 体检
python main.py                :: 后台常驻（看门狗 -> 拉起并守护 guard.py）
python watchdog.py            :: 同上
```

**自己打包 exe：**

```bat
python -m pip install pyinstaller
python tools\build_exe.py     :: 产出仓库根目录的 EdifierGuard.exe
```

---

## 使用

| 目的 | 命令 |
|---|---|
| **体检（推荐先跑）** | `EdifierGuard.exe --doctor` |
| 看当前状态 | `EdifierGuard.exe --status` |
| 列出 HECATE 端点 | `EdifierGuard.exe --list` |
| 只判定一次（含电量 / 在线位 / 对端 MAC） | `EdifierGuard.exe --once` |
| 只观察、不改设备（安全模式） | `EdifierGuard.exe --monitor` |
| 前台详细日志 | `EdifierGuard.exe --debug` |
| 把所有端点恢复为可用 | `EdifierGuard.exe --restore` |
| 解除熔断 | `EdifierGuard.exe --reset-fuse` |
| 停止守护（并恢复端点） | `EdifierGuard.exe --stop` |
| 安装 / 卸载开机自启 | `EdifierGuard.exe --install` / `EdifierGuard.exe --uninstall` |
| 版本 / 帮助 | `EdifierGuard.exe --version` / `EdifierGuard.exe --help` |

> - **不带任何参数**运行 = 静默后台常驻（双击即为此模式）。
> - 从源码运行时，把上面的 `EdifierGuard.exe` 换成 `python main.py`。
> - 命令行输出会接回当前控制台；双击运行一次性命令又没有控制台时，结果用**弹窗**显示，
>   无论哪种方式都**不会写任何文件**。

**图形化 / 脚本入口：**

| 文件 | 作用 |
|---|---|
| `scripts\启动.vbs` | 手动启动（优先用「启动」文件夹里的副本；不写自启、不改注册表） |
| `scripts\停止.bat` | 停止守护，并把端点恢复为可用 |
| `scripts\状态.bat` | 每 3 秒刷新一次的状态面板 |
| `scripts\诊断.bat` | 一键体检（`--doctor`） |
| `scripts\FIX_AUDIO_DEVICE.bat` | 底层 USB 音频接口被禁用时的急救（**需管理员**） |
| `scripts\CLEAN_OLD_TASK.bat` | 清理历史遗留的计划任务（**需管理员**） |
| `tools\gui_server.py` + `tools\preview.html` | 本地「信号观察器」网页（展示原始 HID 报文与判定） |
| `tools\repair_device.py` | 端点状态修复工具（免管理员） |
| `tools\watch.py` | HID 信号观察 / 逆向面包板 |
| `tools\selftest.py` | 自测（`--inject` 会临时弄坏再修复） |

> `scripts\` 下的脚本**与路径无关**：优先用「启动」文件夹里已安装的 exe，
> 其次项目根目录的 `EdifierGuard.exe`，都没有才回退到 Python 源码，所以项目搬到哪都能用。

---

## 配置

**正常使用不需要任何配置**：默认参数写死在 `guard.py` 里，程序**永远不会创建**配置文件。

如果你确实想调（高级用法），把一个 `guard_config.json` **放到 exe 旁边**即可 ——
程序只会「读」它，不会写。仓库里的 [`guard_config.json`](guard_config.json) 就是一份现成的模板。

```json
{
  "mode": "enforce",
  "poll_interval": 0.5,
  "poll_interval_idle": 1.2,
  "idle_after_secs": 25.0,
  "use_online_bit": false,
  "offline_confirm_secs": 3.0,
  "max_apply_failures": 3,
  "auto_repair": true
}
```

| 键 | 默认 | 含义 |
|---|---|---|
| `mode` | `enforce` | `enforce` 实际执行；`monitor` 只观察不操作 |
| `poll_interval` | `0.5` | 快速轮询间隔（秒） |
| `poll_interval_idle` | `1.2` | 长时间稳定后的省电轮询间隔（秒） |
| `idle_after_secs` | `25.0` | 判定稳定多久后进入省电轮询 |
| `use_online_bit` | `false` | `true` = 把「在线标志位」也作为上线依据（更激进） |
| `offline_confirm_secs` | `3.0` | 判定「离线」需持续确认的时长（防接触抖动） |
| `max_apply_failures` | `3` | 连续失败多少次触发熔断 |
| `auto_repair` | `true` | 端点卡在异常态但底层设备在场时自动修复 |

---

## 安全设计与已知边界

- **`SetEndpointVisibility` 是 Windows 未公开接口**，且改的是 **HKLM 系统级**状态——**对这台机器的所有用户生效**（这也是它能免管理员使用的原因）。程序只映射到「启用 / 禁用端点」这一个用途。
- **作用域收敛**：程序**只**操作从 `MMDevices` 中识别出的 **HECATE 自己的端点**，绝不触碰其它音频设备。
- **多重保险**：空集合判失败、操作后独立校验、失败回滚为「可用」、连续失败熔断、启动自愈、`monitor` 只观察模式。
- **已知边界**：耳机**固件 / 厂商驱动更新**可能改变 HID 协议或端点名称，届时需要更新「状态解析 / 端点匹配」。本程序**不是**官方软件，与漫步者/HECATE 无关。

---

## 目录结构

```
EdifierGuard/
├── EdifierGuard.exe         # ★ 单文件程序（自包含：免 Python、免外部 dll/路径、运行期零文件）
├── main.py                  # exe 入口：角色分派 / 控制台接管 / --install / --uninstall
├── guard.py                 # 核心守护进程（检测 → 判定 → 动作 → 校验）
├── watchdog.py              # 看门狗：退出/卡死自动重启
├── ipc.py                   # 进程间状态（共享内存 + 具名互斥体，运行期不落任何文件）
├── guard_config.json        # 可选配置模板（只读，程序不会创建/修改）
├── lib/hid/                 # 内置 hid 模块（纯 ctypes 封装，见第三方许可）
├── runtime/hidapi.dll       # 内置 HID 动态库（源码运行用；exe 里已内含）
├── docs/                    # 文档（协议、设计、复盘）
├── tools/                   # 开发 / 诊断工具（路径无关）
│   ├── build_exe.py         #   打包成单文件 exe
│   ├── selftest.py          #   自测 / 故障注入
│   ├── monitor.py           #   实时监控面板
│   ├── watch.py             #   HID 信号观察（逆向面包板）
│   ├── gui_server.py        #   本地「信号观察器」网页服务
│   ├── preview.html         #   上述网页前端
│   ├── repair_device.py     #   端点状态修复
│   └── _bootstrap.py        #   向上定位项目根（让 tools/ 可任意摆放）
└── scripts/                 # 启动 / 停止 / 诊断脚本
    ├── 启动.vbs             #   手动启动
    ├── 停止.bat             #   停止 + 恢复端点
    ├── 状态.bat             #   状态面板
    ├── 诊断.bat             #   一键体检
    ├── _env.bat             #   内部辅助：探测该用哪个 exe / python
    ├── FIX_AUDIO_DEVICE.bat #   底层音频接口急救（需管理员）
    └── CLEAN_OLD_TASK.bat   #   清理遗留计划任务（需管理员）
```

### 运行数据：**没有**

程序在任何情况下都**不会创建**日志 / 配置 / pid / 心跳 / 状态等文件：

- guard 与看门狗之间的协作走 **共享内存 + 具名互斥体**（见 `ipc.py`），
  所有进程退出后由系统自动回收，不留痕迹；
- 唯一的例外（**默认关闭**）是排查用的日志 —— 设了环境变量才写：

```bat
set EDIFIERGUARD_LOG=%TEMP%\edifierguard.log
EdifierGuard.exe --debug
```

> 也就是说：程序本体、运行痕迹全在 `EdifierGuard.exe` 这一个文件里，没有第二处。
> 想彻底清除：先 `EdifierGuard.exe --uninstall`，再删掉「启动」文件夹里那个 exe 即可。

---

## 常见问题

**Q：为什么不用「切换默认设备」？**
A：两者解决不同问题。「切换默认设备」只是改路由，设备本身仍然存在、仍可被应用选中；而这里端点**永远 `ACTIVE`**，只有「隐藏端点」能让它真正消失。取舍详见 [docs/design-notes.md](docs/design-notes.md)。

**Q：会不会把我自己的耳机永久隐藏了？**
A：不会。这是 v1 的教训——v2 有「空集合=失败」「操作后独立校验」「失败回滚为可用」「启动自愈」「熔断」五重保护；任何异常都会让设备**回到可用**。

**Q：我把两个守护进程都强杀了，设备还隐藏着怎么办？**
A：下次登录（自启）或双击 `scripts\启动.vbs` 即会自愈；也可直接 `EdifierGuard.exe --restore`。

**Q：日志在哪？**
A：**默认没有日志** —— 程序运行期不产生任何文件。需要排查问题时临时开一个：
`set EDIFIERGUARD_LOG=%TEMP%\edifierguard.log`，再运行 `EdifierGuard.exe --debug`。

**Q：它会在别的目录里放文件吗？**
A：只会在 `%TEMP%` 下建一个打包解包目录 `_MEIxxxxxx`（约 16 MB，单文件 exe 的固有行为），
**正常退出时自动删除**；被强杀（任务管理器结束进程）时可能残留，
跑一次 `EdifierGuard.exe --stop` / `--install` / `--uninstall` 就会顺手清理掉。
除此之外**不写任何文件**，也不建安装目录、不写注册表、不建服务。

**Q：项目文件夹删了会怎样？**
A：把 `EdifierGuard.exe` 放进「启动」文件夹之后（`--install` 就是干这个），
**那一个文件就是全部**，项目删掉也照常自启、照常工作。
想彻底移除：先跑 `EdifierGuard.exe --uninstall`（会停止并移除自启），再删掉「启动」文件夹里的那个 exe。

**Q：需要管理员吗？**
A：不需要。只有 `scripts\FIX_AUDIO_DEVICE.bat` / `scripts\CLEAN_OLD_TASK.bat` 这两个「急救 / 清理」脚本需要。

**Q：会不会影响音质或断开耳机？**
A：不会。程序只切换**端点可见性**，从不动 PnP、不动驱动、不断链路。

---

## 免责声明

本项目为**非官方**社区工具，与漫步者 / HECATE / 杰理科技无关，也未获其授权或支持。它使用了**逆向得到的只读状态查询报文**与 **Windows 未公开的音频策略接口**；耳机固件、厂商驱动或 Windows 更新都可能使其失效。请自行评估风险后使用。

---

## 许可证与第三方

- 本项目以 **MIT** 许可证发布，见 [LICENSE](LICENSE)。
- 内置第三方组件（均为**三选一许可**，本项目按其宽松条款使用）：
  - [`cython-hidapi`](https://github.com/trezor/cython-hidapi)（`lib/hid/`）
  - [`hidapi`](https://github.com/libusb/hidapi)（`runtime/hidapi.dll`）
  - 详见 [THIRD_PARTY_LICENSES.md](docs/THIRD_PARTY_LICENSES.md)。

---

## 相关项目

同类 / 可参考的开源项目（本项目与它们无关联，仅致谢与对照）：

- [saad780/blackshark-audio-switcher](https://github.com/saad780/blackshark-audio-switcher) — 最接近的同类（同样是「dongle 常驻、轮询 HID」，但它切换默认设备）
- [Ayerdi/PROX2-AutoSwitch](https://github.com/Ayerdi/PROX2-AutoSwitch) — 无线耳机自动切换默认输出，安全规则值得借鉴
- [Sapd/HeadsetControl](https://github.com/Sapd/HeadsetControl) / [HeyOkay/HaloBattery](https://github.com/HeyOkay/HaloBattery) — 大量厂商 HID 协议资料
- [Belphemur/SoundSwitch](https://github.com/Belphemur/SoundSwitch) — 默认设备切换 / profile 触发器
- `IPolicyConfig` 定义参考：`tartakynov/audioswitch`、`DanStevens/AudioEndPointController`、`sidit77/com-policy-config`
