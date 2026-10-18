# -*- coding: utf-8 -*-
"""
Edifier Guard v2.0 —— 漫步者 HECATE G3 Max 音频设备自动跟随（加固版）

与 v1 的区别（v1 曾把用户耳机"永久屏蔽"，详见 经验总结与改进说明.md）
--------------------------------------------------------------------
v1 的致命问题：
  1. set_device() 在"可操作端点集合"为空时仍然 return True —— 假成功
  2. live 过滤把 NOTPRESENT 端点排除在"可操作"之外 —— 卡住就永远救不回来
  3. 用缓存的 cur_state 短路，一旦自认为成功就再也不校验 —— 死锁
  4. 只用注册表 DeviceState 判断"设备是否可用" —— 看不到 PnP 禁用这一层
  5. 把"禁用设备"和"隐藏设备"混为一谈，还硬写 DeviceState，造出僵尸端点

v2 的六条铁律：
  ① 空集合 = 失败，绝不假成功           -> apply_visible() 显式返回原因
  ② 操作后必须独立验证，不信自己的返回值  -> IMMDeviceEnumerator 活跃列表 + IAudioEndpointVolume
  ③ 验证不过就回滚到"可见"（安全方向）    -> 宁可设备多显示，也不让它消失
  ④ 隐藏必须落到 DISABLED，落到 NOTPRESENT 判为失败并回滚
  ⑤ PnP 出问题（问题码 22 等）就完全不动端点，只报告
  ⑥ 连续失败自动熔断，落 guard.fused，只观察不操作

音频端点状态（注册表 MMDevices\\Audio\\...，注意与 mmdeviceapi.h 的常量不是一套）：
    1          ACTIVE        正常可用
    2          UNPLUGGED     已拔出
    4          NOTPRESENT    未接入（声音设置里看不到）
    0x10000001 DISABLED      被禁用（在"已禁用"列表里）

用法：
    python guard.py               正常守护（按配置，默认 enforce）
    python guard.py --doctor      体检：一次性把问题说清楚（推荐先跑这个）
    python guard.py --status      打印当前状态
    python guard.py --list        列出 HECATE 音频端点
    python guard.py --once        只判定一次
    python guard.py --monitor     只观察不操作（安全模式）
    python guard.py --restore     把所有端点恢复为"可用"
    python guard.py --reset-fuse  解除熔断
    python guard.py --debug       前台详细日志
"""
import atexit
import ctypes
import ctypes.wintypes as wt
import json
import os
import signal
import sys
import time
import winreg

VERSION = "2.0"

# ---------------- 常量 ----------------
VID = 0x35BB
PID = 0xA22B

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, "guard.log")
PID_FILE = os.path.join(HERE, "guard.pid")
HB_FILE = os.path.join(HERE, "guard.heartbeat")
FUSE_FILE = os.path.join(HERE, "guard.fused")
CONFIG_FILE = os.path.join(HERE, "guard_config.json")

BATT_TIMEOUT = 2.0          # 超过这么久没读到有效数据 -> unknown
GRACE_ON_BOOT = 5.0         # 启动宽限期
VERIFY_SETTLE = 0.20        # 操作后等系统收敛
VERIFY_EVERY = 15.0         # 巡检间隔：即使"自认为成功"也要定期复核
HEARTBEAT_EVERY = 2.0

DEFAULT_CONFIG = {
    "mode": "enforce",              # enforce | monitor
    "poll_interval": 0.5,           # HID 轮询（读电量必须快）
    "poll_interval_idle": 1.2,      # 长时间无变化后的省电轮询间隔
    "idle_after_secs": 25.0,        # 判定稳定多久后进入省电轮询
    "use_online_bit": False,        # True=把"在线标志位"也作为上线依据（更激进）
    "offline_confirm_secs": 3.0,    # 判定"离线"需持续这么久（防接触抖动）
    "max_apply_failures": 3,        # 连续失败多少次 -> 熔断
    "auto_repair": True,            # 端点卡在 4/2 但底层设备在场时自动修复
}

# 自包含：优先使用随程序分发的 lib/ 目录
_LOCAL_LIB = os.path.join(HERE, "lib")
if os.path.isdir(_LOCAL_LIB) and _LOCAL_LIB not in sys.path:
    sys.path.insert(0, _LOCAL_LIB)


# ---------------- 日志 ----------------
def _cli():
    return any(a in sys.argv for a in (
        "--debug", "--once", "--status", "--list", "--doctor", "--restore",
        "--reset-fuse", "--selftest"))


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    if _cli():
        try:
            print(line, flush=True)
        except Exception:
            pass
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 512 * 1024:
            os.replace(LOG_FILE, LOG_FILE + ".old")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------- 配置 ----------------
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                for k in DEFAULT_CONFIG:
                    if k in user:
                        cfg[k] = user[k]
    except Exception as e:
        log("配置文件读取失败，使用默认值: %s" % e)
    return cfg


def save_default_config():
    if os.path.isfile(CONFIG_FILE):
        return
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _remove_pid():
    """退出时清理 PID 文件。
    ★ 不清理会在硬杀/崩溃后留下旧 PID；等系统把这个 PID 复用给别的进程，
      看门狗读到它就会去"重启卡死的 guard"，把无关进程杀掉。
      这里在正常/信号退出路径清掉，配合看门狗的映像名校验双保险。
    """
    try:
        if os.path.isfile(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ---------------- 单实例 ----------------
_MUTEX_NAME = "Global\\EdifierGuard_G3Max_SingleInstance"
# 注意：Global\\ 命名空间在非管理员下可能拿不到（err=5），
# 那种情况说明"有一个更高权限的实例存在"，一律当已有实例处理 ——
# v1 这里返回 True 导致高低权限两个实例同时跑，互相打架。
_mutex_handle = None


def acquire_single_instance():
    global _mutex_handle
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wt.HANDLE
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
        h = k32.CreateMutexW(None, True, _MUTEX_NAME)
        err = ctypes.get_last_error()
        if not h:
            return False
        if err == 183:            # ERROR_ALREADY_EXISTS
            k32.CloseHandle(h)
            return False
        if err == 5:              # ERROR_ACCESS_DENIED —— 已有高权限实例
            k32.CloseHandle(h)
            return False
        _mutex_handle = h
        return True
    except Exception:
        return True


# ================= HID 电量读取（沿用 v1 的调优参数，实测命中率 100%） =================
class BatteryReader:
    def __init__(self):
        self.mode = None
        self.dev = None
        self.path = None
        self.query = b""
        self.can_write = False
        self.last_query = 0.0
        self.mac = None          # 最近一次 op=0x07 收到的"对端(耳机)MAC"
        self.mac_ts = 0.0
        self._init()

    def _init_hidlib(self):
        try:
            for cand in _dll_dirs():
                try:
                    os.add_dll_directory(cand)
                except Exception:
                    pass
            import hid  # noqa

            devs = list(hid.enumerate(VID, PID))
            if not devs:
                return False

            # 必须选 Col03 —— 实测只有它能写入查询帧并收到回复
            pick = None
            for d in devs:
                p = d["path"]
                pstr = p.decode("mbcs", "replace") if isinstance(p, (bytes, bytearray)) else str(p)
                if "Col03" in pstr:
                    pick = d
                    break
            if pick is None:
                for d in devs:
                    if d.get("usage_page", 0) == 0xFF02:
                        pick = d
                        break
            if pick is None:
                pick = devs[0]

            self.dev = hid.Device(path=pick["path"])
            self.dev.nonblocking = 1
            self.path = pick["path"]
            pstr = (pick["path"].decode("mbcs", "replace")
                    if isinstance(pick["path"], (bytes, bytearray))
                    else str(pick["path"]))
            self.query = bytes([0xD0, 0x35, 0xBB, 0x01]) + bytes(60)
            self.can_write = "Col03" in pstr
            self.mode = "hidlib"
            return True
        except Exception:
            return False

    def _init(self):
        if self._init_hidlib():
            return
        self.mode = "none"

    def close(self):
        try:
            if self.dev:
                self.dev.close()
        except Exception:
            pass
        self.dev = None

    def read(self):
        """返回 (battery, raw)。battery=None 表示没读到；-1 表示简帧（无电量字节）"""
        if self.mode != "hidlib" or self.dev is None:
            return None, None
        try:
            if self.can_write:
                try:
                    self.dev.write(self.query)
                    self.last_query = time.time()
                except Exception:
                    pass

            b = None
            raw = None
            idle = 0
            for _ in range(20):
                try:
                    r = self.dev.read(64, timeout=1)
                except Exception:
                    break
                if not r:
                    idle += 1
                    if b is not None and idle >= 1:
                        break
                    if idle >= 8:
                        break
                    continue
                idle = 0
                rb = bytes(r)
                if len(rb) >= 11 and rb[3] == 0x07:
                    # 对端(耳机)MAC：离线时不出现，上线时出现 —— 最贴近"链路已建立"的标记
                    self.mac = rb[5:11].hex()
                    self.mac_ts = time.time()
                if len(rb) >= 5 and rb[3] == 0x0A:
                    bb, _r = self._parse(rb)
                    b = bb
                    raw = rb
                elif raw is None:
                    raw = rb
            return b, raw
        except Exception:
            try:
                self.close()
            except Exception:
                pass
            self._init()
            return None, None

    @staticmethod
    def _parse(r):
        if len(r) >= 5 and r[0] == 0xD0 and r[1] == 0x35 and r[2] == 0xBB:
            op = r[3]
            payload = r[4:]
            if op == 0x0A:
                if len(payload) > 2:
                    v = payload[2]
                    if 0 <= v <= 100:
                        return v, r
                    return -1, r
                return -1, r
        return None, r

    @staticmethod
    def online_bit(r):
        if r and len(r) > 5:
            return r[5]
        return None


def _dll_dirs():
    dirs = [HERE,
            os.path.join(HERE, "runtime"),
            os.path.join(HERE, "lib", "hid"),
            os.path.join(HERE, "_internal"),
            os.path.join(HERE, "_internal", "hid")]
    try:
        import sysconfig
        sp = sysconfig.get_paths().get("purelib")
        if sp:
            dirs.append(os.path.join(sp, "hid"))
    except Exception:
        pass
    for p in (r"C:\Program Files\HECATE", r"C:\Program Files (x86)\HECATE"):
        if os.path.isdir(p):
            dirs.append(p)
    return [d for d in dirs if d and os.path.isdir(d)]


# ================= 音频端点（注册表） =================
_RENDER_BASE = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
_CAPTURE_BASE = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
_PKEY_NAME = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"
_PKEY_DEVINST = "{b3f8fa53-0004-438e-9003-51a46e139bfc},2"

STATE_ACTIVE = 1
STATE_DISABLED = 0x10000001
STATE_NOTPRESENT = 4
STATE_UNPLUGGED = 2

_STATE_NAME = {
    1: "ACTIVE可用",
    2: "UNPLUGGED已拔出",
    4: "NOTPRESENT未接入",
    0x10000001: "DISABLED已禁用",
}


def state_name(st):
    return _STATE_NAME.get(st, str(st) if st is not None else "?")


def strip_prefix(devinst):
    """去掉注册表 devinst 里的 `{N}.` 前缀（这是 MMDevices 的正常格式，不是污染）"""
    s = (devinst or "").upper()
    if s.startswith("{") and "}." in s:
        s = s.split("}.", 1)[1]
    return s


def enum_endpoints():
    """枚举 Render + Capture 全部端点（不含 PnP 判定，那一步在 classify 里做）"""
    out = []
    for kind, base in (("Render", _RENDER_BASE), ("Capture", _CAPTURE_BASE)):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base, 0,
                                winreg.KEY_READ) as k:
                n = winreg.QueryInfoKey(k)[0]
                for i in range(n):
                    sub = winreg.EnumKey(k, i)
                    nm = di = None
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                            base + "\\" + sub + "\\Properties", 0,
                                            winreg.KEY_READ) as pk:
                            try:
                                nm, _ = winreg.QueryValueEx(pk, _PKEY_NAME)
                            except Exception:
                                pass
                            try:
                                di, _ = winreg.QueryValueEx(pk, _PKEY_DEVINST)
                            except Exception:
                                pass
                    except Exception:
                        continue
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                            base + "\\" + sub, 0,
                                            winreg.KEY_READ) as sk:
                            st, _ = winreg.QueryValueEx(sk, "DeviceState")
                    except FileNotFoundError:
                        st = None
                    except Exception:
                        st = None
                    flow = "1" if kind == "Capture" else "0"
                    out.append(dict(kind=kind, guid=sub, name=nm or "",
                                    devid="{0.0.%s.00000000}.%s" % (flow, sub.upper()),
                                    state=st, devinst=di,
                                    pnp_present=False, pnp_problem=None))
        except Exception:
            pass
    return out


# ================= PnP 层（最根本的一层） =================
_INVALID_H = ctypes.c_void_p(-1).value
_PROBLEM_NAME = {
    0: "正常运行", 1: "未配置", 3: "驱动损坏", 10: "无法启动", 12: "资源不足",
    14: "需要重启", 18: "需要重装驱动", 19: "注册表损坏", 21: "正在移除",
    22: "已被禁用（必须管理员启用）", 24: "设备未连接", 28: "驱动未安装",
    31: "驱动加载失败", 43: "已被停止", 45: "当前未连接",
}


def pnp_snapshot():
    """一次 SetupAPI 扫描，返回 {实例ID大写: (问题码, 状态位)}。
    只含"在场"设备（含被禁用的 —— 被禁用的设备仍然在场，这正是关键）。
    """
    out = {}
    try:
        setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
        cfg = ctypes.WinDLL("cfgmgr32", use_last_error=True)
        setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
        setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wt.LPCWSTR,
                                                 wt.HWND, wt.DWORD]
        setupapi.SetupDiEnumDeviceInfo.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p]
        setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                                         wt.LPWSTR, wt.DWORD,
                                                         ctypes.POINTER(wt.DWORD)]
        setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
        cfg.CM_Get_DevNode_Status.argtypes = [ctypes.POINTER(wt.ULONG),
                                              ctypes.POINTER(wt.ULONG),
                                              wt.DWORD, wt.ULONG]
        cfg.CM_Get_DevNode_Status.restype = wt.ULONG

        class SP_DEVINFO_DATA(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("ClassGuid", ctypes.c_ubyte * 16),
                        ("DevInst", wt.DWORD), ("Reserved", ctypes.c_void_p)]

        # ★ 必须 ALLCLASSES|PRESENT(0x06)，只用 PRESENT(0x02) 会枚举到 0 个
        h = setupapi.SetupDiGetClassDevsW(None, None, None, 0x06)
        if not h or h == _INVALID_H:
            return out
        i = 0
        while True:
            d = SP_DEVINFO_DATA()
            d.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not setupapi.SetupDiEnumDeviceInfo(h, i, ctypes.byref(d)):
                break
            b = ctypes.create_unicode_buffer(1024)
            nn = wt.DWORD()
            if setupapi.SetupDiGetDeviceInstanceIdW(h, ctypes.byref(d), b, 1024,
                                                    ctypes.byref(nn)):
                st = wt.ULONG(0)
                pb = wt.ULONG(0)
                if cfg.CM_Get_DevNode_Status(ctypes.byref(st), ctypes.byref(pb),
                                             d.DevInst, 0) == 0:
                    out[b.value.upper()] = (pb.value, st.value)
            i += 1
        setupapi.SetupDiDestroyDeviceInfoList(ctypes.c_void_p(h))
    except Exception:
        pass
    return out


def pnp_status(inst):
    """★ 定点查询单个 PnP 实例，替代"全量扫描 299 个设备"。

    返回 (问题码, 状态位)；实例不在场（或查不到）时返回 None。
    仅目标那 2~4 个 devinst 各查一次，比 pnp_snapshot() 快一个数量级。
    """
    if not inst:
        return None
    try:
        cfg = ctypes.WinDLL("cfgmgr32", use_last_error=True)
        cfg.CM_Locate_DevNodeW.argtypes = [ctypes.POINTER(wt.ULONG),
                                           wt.LPCWSTR, wt.ULONG]
        cfg.CM_Locate_DevNodeW.restype = wt.ULONG
        cfg.CM_Get_DevNode_Status.argtypes = [ctypes.POINTER(wt.ULONG),
                                              ctypes.POINTER(wt.ULONG),
                                              wt.ULONG, wt.ULONG]
        cfg.CM_Get_DevNode_Status.restype = wt.ULONG
        dn = wt.ULONG(0)
        # ulFlags=0 (CM_LOCATE_DEVNODE_NORMAL)：不在场的设备会返回非 0 (CR_NO_SUCH_DEVINST)
        if cfg.CM_Locate_DevNodeW(ctypes.byref(dn), inst, 0) != 0:
            return None
        st = wt.ULONG(0)
        pb = wt.ULONG(0)
        if cfg.CM_Get_DevNode_Status(ctypes.byref(st), ctypes.byref(pb),
                                     dn.value, 0) != 0:
            return None
        return (pb.value, st.value)
    except Exception:
        return None


def classify(eps):
    """给端点补上 PnP 信息，并算出三个层次的能力标志。

    pnp_present : 底层设备实例当前在场（含被禁用）—— 决定"能不能救"
    pnp_problem : 0=正常，22=被禁用，None=设备不在场
    usable      : 在场 + ACTIVE  —— 决定"现在能不能出声"
    operable    : 在场（不论 state）—— 决定"值不值得对它调可见性"
    """
    cache = {}
    for e in eps:
        inst = strip_prefix(e.get("devinst"))
        # ★ 定点查询（每个实例只查一次），替代"全量扫描 299 个设备"
        if inst not in cache:
            cache[inst] = pnp_status(inst)
        hit = cache[inst]
        e["pnp_present"] = hit is not None
        e["pnp_problem"] = hit[0] if hit else None
        e["usable"] = e["pnp_present"] and e["state"] == STATE_ACTIVE
        e["operable"] = e["pnp_present"]
        # v1 的 live 语义保留给 --list/--status 展示：在场且不是 NOTPRESENT
        e["live"] = e["pnp_present"] and e["state"] != STATE_NOTPRESENT
    return eps


def target_endpoints():
    """挑出需要跟随耳机开关的 HECATE 端点。
    排除 Hands-Free（通话端点，动了会破坏通话）。
    """
    res = []
    for e in enum_endpoints():
        nm = e["name"] or ""
        di = (e.get("devinst") or "").upper()
        name_hit = "HECATE" in nm
        inst_hit = "HECATE" in di
        usb_hit = "VID_35BB" in di
        bth_hit = "000105D6" in di
        if not (name_hit or inst_hit or usb_hit or bth_hit):
            continue
        if "Hands-Free" in nm or "BTHHFENUM" in di:
            continue
        res.append(e)
    return res


def targets_classified():
    return classify(target_endpoints())


def current_state(eps=None):
    """'visible' / 'hidden' / 'mixed' / 'absent'
    只统计底层在场（operable）的端点 —— 不在场的端点不参与判断，
    否则耳机遇尔没插好就会被误判成"已隐藏"。
    """
    eps = eps if eps is not None else targets_classified()
    live = [e for e in eps if e["operable"]]
    if not live:
        return "absent"
    states = [e["state"] for e in live]
    if any(s == STATE_ACTIVE for s in states):
        return "visible" if all(s == STATE_ACTIVE for s in states) else "mixed"
    if all(s == STATE_DISABLED for s in states):
        return "hidden"
    if any(s == STATE_DISABLED for s in states):
        return "mixed"
    return "absent"


def write_devicestate(e, value):
    """直接改端点 DeviceState。
    ⚠️ 只在"底层设备确实在场"时使用 —— 对不在场的端点硬写 1 会造出僵尸端点。
    """
    if not e.get("pnp_present"):
        return False
    base = _RENDER_BASE if e["kind"] == "Render" else _CAPTURE_BASE
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + "\\" + e["guid"], 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "DeviceState", 0, winreg.REG_DWORD, value)
        return True
    except Exception:
        return False


# ================= Windows 音频 API（独立验证的唯一裁判） =================
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD),
                ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


def _g(a, b, c, d):
    return _GUID(a, b, c, (ctypes.c_ubyte * 8)(*d))


_CLSID_MMDE = _g(0xBCDE0395, 0xE52F, 0x467C,
                 (0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E))
_IID_IMMDE = _g(0xA95664D2, 0x9614, 0x4F35,
                (0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6))
# IAudioEndpointVolume —— 激活它能证明"KS 音频链路真的通了"
_IID_IAudioEndpointVolume = _g(0x5CDF2C82, 0x841E, 0x4546,
                              (0x97, 0x22, 0x0C, 0xF7, 0x40, 0x78, 0x22, 0x9A))
_VTP = ctypes.POINTER(ctypes.c_void_p)

HR_PATH_NOT_FOUND = 0x80070003     # 端点存在但 KS 拓扑没了 —— "僵尸端点"特征码
HR_NOT_FOUND = 0x80070490
HR_E_POINTER = 0x80004003


def _vfn(ptr, idx, *argtypes):
    """取 COM 接口第 idx 个虚函数（this 显式作为第一个参数）"""
    vt = ctypes.cast(ptr, _VTP)[0]
    faddr = ctypes.cast(vt, _VTP)[idx]
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(faddr)


class AudioAPI:
    """IMMDeviceEnumerator 的最小封装。
    v1 只看注册表 —— 注册表说 ACTIVE 不代表音频链路真的通。
    """

    def __init__(self):
        self.ok = False
        self._ole = None
        self._en = None
        self._co = False
        try:
            self._ole = ctypes.WinDLL("ole32")
            self._ole.CoInitializeEx.restype = ctypes.c_long
            self._ole.CoCreateInstance.restype = ctypes.c_long
            self._ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            hr = self._ole.CoInitializeEx(None, 2)      # APARTMENTTHREADED
            self._co = hr in (0, 1)
            if (hr & 0xFFFFFFFF) in (0x80010106,):     # RPC_E_CHANGED_MODE
                self._co = False
            en = ctypes.c_void_p()
            hr = self._ole.CoCreateInstance(ctypes.byref(_CLSID_MMDE), None, 1,
                                            ctypes.byref(_IID_IMMDE),
                                            ctypes.byref(en))
            if hr != 0 or not en.value:
                return
            self._en = ctypes.c_void_p(en.value)
            self.ok = True
        except Exception:
            self.ok = False

    def active_ids(self):
        """返回 {0:set(小写devid), 1:set(...)}：声音设置里"能用"的端点"""
        res = {0: set(), 1: set()}
        if not self.ok:
            return res
        try:
            for flow in (0, 1):
                coll = ctypes.c_void_p()
                # IMMDeviceEnumerator vtable[3] = EnumAudioEndpoints
                _vfn(self._en.value, 3, wt.DWORD, wt.DWORD,
                     ctypes.POINTER(ctypes.c_void_p))(
                    self._en, flow, 1, ctypes.byref(coll))     # 1 = DEVICE_STATE_ACTIVE
                if not coll.value:
                    continue
                cnt = ctypes.c_uint(0)
                _vfn(coll.value, 3, ctypes.POINTER(ctypes.c_uint))(
                    ctypes.c_void_p(coll.value), ctypes.byref(cnt))
                for i in range(cnt.value):
                    dev = ctypes.c_void_p()
                    # IMMDeviceCollection vtable[4] = Item
                    _vfn(coll.value, 4, ctypes.c_uint,
                         ctypes.POINTER(ctypes.c_void_p))(
                        ctypes.c_void_p(coll.value), i, ctypes.byref(dev))
                    if not dev.value:
                        continue
                    sid = ctypes.c_void_p()
                    # IMMDevice vtable[5] = GetId
                    _vfn(dev.value, 5, ctypes.POINTER(ctypes.c_void_p))(
                        ctypes.c_void_p(dev.value), ctypes.byref(sid))
                    if sid.value:
                        res[flow].add(ctypes.wstring_at(sid.value).lower())
                        self._ole.CoTaskMemFree(sid)
                    _vfn(dev.value, 2)(ctypes.c_void_p(dev.value))   # Release
                _vfn(coll.value, 2)(ctypes.c_void_p(coll.value))
        except Exception:
            pass
        return res

    def activate_hr(self, devid):
        """尝试 Activate(IAudioEndpointVolume)，返回 HRESULT；None=调不通"""
        if not self.ok:
            return None
        try:
            dev = ctypes.c_void_p()
            # IMMDeviceEnumerator vtable[5] = GetDevice
            hr = _vfn(self._en.value, 5, ctypes.c_wchar_p,
                      ctypes.POINTER(ctypes.c_void_p))(
                self._en, devid, ctypes.byref(dev))
            if hr != 0 or not dev.value:
                return hr & 0xFFFFFFFF
            vol = ctypes.c_void_p()
            # IMMDevice vtable[3] = Activate
            hr2 = _vfn(dev.value, 3, ctypes.POINTER(_GUID), wt.DWORD,
                       ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
                ctypes.c_void_p(dev.value), ctypes.byref(_IID_IAudioEndpointVolume),
                1, None, ctypes.byref(vol))
            if vol.value:
                _vfn(vol.value, 2)(ctypes.c_void_p(vol.value))
            _vfn(dev.value, 2)(ctypes.c_void_p(dev.value))
            return hr2 & 0xFFFFFFFF
        except Exception:
            return None

    def close(self):
        try:
            if self._en:
                _vfn(self._en.value, 2)(self._en)     # Release
        except Exception:
            pass
        self._en = None
        self.ok = False
        try:
            if self._co and self._ole:
                self._ole.CoUninitialize()
        except Exception:
            pass
        self._co = False


# ================= IPolicyConfig（隐藏/显示端点） =================
_CLSID_PolicyConfigClient = _g(0x870AF99C, 0x171D, 0x4F9E,
                               (0xAF, 0x0D, 0xE6, 0x3D, 0xF4, 0x0C, 0x2B, 0xC9))
_IID_IPolicyConfig = _g(0xF8679F50, 0x850A, 0x41CF,
                        (0x9C, 0x72, 0x43, 0x0F, 0x29, 0x02, 0x90, 0xC8))
_VT_SET_ENDPOINT_VISIBILITY = 14
_VT_RELEASE = 2


class PolicyConfig:
    def __init__(self):
        self.ok = False
        self._p = None
        self._ole = None
        self._setvis = None
        self._release = None
        self._co = False
        try:
            self._ole = ctypes.WinDLL("ole32")
            self._ole.CoInitializeEx.restype = ctypes.c_long
            self._ole.CoCreateInstance.restype = ctypes.c_long
            hr = self._ole.CoInitializeEx(None, 2)
            self._co = hr in (0, 1)
            p = ctypes.c_void_p()
            hr = self._ole.CoCreateInstance(ctypes.byref(_CLSID_PolicyConfigClient),
                                            None, 1,
                                            ctypes.byref(_IID_IPolicyConfig),
                                            ctypes.byref(p))
            if hr != 0 or not p.value:
                return
            self._p = p
            vtbl = ctypes.cast(p, _VTP)[0]
            funcs = ctypes.cast(vtbl, _VTP)
            self._setvis = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p,
                ctypes.c_int)(funcs[_VT_SET_ENDPOINT_VISIBILITY])
            self._release = ctypes.WINFUNCTYPE(
                ctypes.c_ulong, ctypes.c_void_p)(funcs[_VT_RELEASE])
            self.ok = True
        except Exception:
            self.ok = False

    def set_visible(self, devid, visible):
        if not self.ok:
            return False
        try:
            hr = self._setvis(self._p, devid, 1 if visible else 0)
            return (hr & 0xFFFFFFFF) == 0
        except Exception:
            return False

    def close(self):
        try:
            if self._release and self._p:
                self._release(self._p)
        except Exception:
            pass
        self._p = None
        self.ok = False
        try:
            if self._co and self._ole:
                self._ole.CoUninitialize()
        except Exception:
            pass
        self._co = False


_policy = None


def _get_policy():
    global _policy
    if _policy is None or not _policy.ok:
        _policy = PolicyConfig()
    return _policy


# ================= 核心：带验证与回滚的可见性切换 =================
def _targets_for(eps, visible):
    """本次操作应作用的端点。

    visible=True  -> 所有底层在场的端点（含 state=4/2/DISABLED），它们都需要"变可见"
    visible=False -> 只处理当前 ACTIVE 的端点。已经隐藏的不用动；
                     NOTPRESENT 的绝对不能碰（碰了就是僵尸端点）
    底层 PnP 有问题的端点：两种方向都不碰 —— 端点层操作治不了它，只会更乱
    """
    if visible:
        return [e for e in eps if e["operable"] and not e["pnp_problem"]]
    return [e for e in eps
            if e["operable"] and not e["pnp_problem"]
            and e["state"] == STATE_ACTIVE]


def _flow_of(e):
    return 1 if e["kind"] == "Capture" else 0


def _check(e, visible, active, fresh, api):
    """检查单个端点是否真的达到了目标状态。
    返回 (ok:bool, problem:str|None, hr:int|None)
    """
    in_active = e["devid"].lower() in active[_flow_of(e)]
    cur = fresh.get(e["devid"])
    if visible:
        if not in_active:
            if cur is None:
                return False, "endpoint-gone", None
            if cur.get("pnp_problem"):
                return False, "pnp-problem-%s" % cur["pnp_problem"], None
            if not cur.get("pnp_present"):
                return False, "device-absent", None
            return False, "not-in-active-list(state=%s)" % state_name(cur.get("state")), None
        # 在活跃列表里，再确认音频链路真的通（v1 缺的就是这一步）
        hr = api.activate_hr(e["devid"])
        if hr is None:
            return True, None, None          # 调不通 API，不否定
        if hr in (HR_PATH_NOT_FOUND, HR_NOT_FOUND):
            return False, "zombie-endpoint(hr=0x%08X)" % hr, hr
        return True, None, hr
    else:
        if in_active:
            return False, "still-active", None
        if cur is None:
            return False, "endpoint-gone", None
        if cur.get("state") == STATE_NOTPRESENT:
            # ★★ 最危险的一种："隐藏"把端点推到了 NOTPRESENT。
            #    这正是 v1 让用户耳机彻底消失的方式。判为失败，必须回滚。
            return False, "hide-became-notpresent", None
        if cur.get("state") == STATE_DISABLED:
            return True, None, None
        return False, "unexpected-state(%s)" % state_name(cur.get("state")), None


def apply_visible(visible, reason="", allow_repair=True):
    """把 HECATE 端点切换到 可见/隐藏。

    返回 (ok:bool, detail:str)
      ok=True  表示已经用 Windows 音频 API 验证通过
      ok=False 时 detail 说明原因（'no-target-endpoint' / 'no-present-endpoint' /
               'pnp-disabled' / 'policy-unavailable' / 'hide-became-notpresent' ...）

    铁律①：集合为空一律返回 False（除"隐藏时本来就没有活跃端点"这种真达成）。
    铁律②：不信 SetEndpointVisibility 的返回值，只信 IMMDeviceEnumerator。
    铁律③：隐藏不达标就回滚成可见。
    """
    eps = targets_classified()
    if not eps:
        return False, "no-target-endpoint"

    # PnP 被禁用（问题码 22 等）：端点层做什么都没意义，直接如实报告
    bad = [e for e in eps if e["pnp_problem"]]
    if bad and not any(e["operable"] and not e["pnp_problem"] for e in eps):
        codes = sorted(set(e["pnp_problem"] for e in bad))
        return False, "pnp-problem-%s" % ",".join(str(c) for c in codes)

    targets = _targets_for(eps, visible)
    if not targets:
        if visible:
            # ★ v1 在这里 return True（假成功），是本次事故的直接原因
            return False, "no-present-endpoint"
        return True, "already-hidden"

    api = AudioAPI()
    try:
        active = api.active_ids()
        need = []
        for e in targets:
            if visible:
                if e["devid"].lower() not in active[_flow_of(e)]:
                    need.append(e)
            else:
                need.append(e)      # targets 已经过滤为 ACTIVE
        if not need:
            return True, "already-visible" if visible else "already-hidden"
        if not api.ok:
            return False, "audio-api-unavailable"

        pc = _get_policy()
        if not pc.ok:
            return False, "policy-unavailable"

        calls = 0
        for e in need:
            if pc.set_visible(e["devid"], visible):
                calls += 1
        time.sleep(VERIFY_SETTLE)

        active = api.active_ids()
        fresh = dict((e["devid"], e) for e in targets_classified())

        problems = []
        for e in need:
            ok, why, hr = _check(e, visible, active, fresh, api)
            if ok:
                continue
            # ---- 修复尝试：只在"变可见"方向，且底层设备确实在场 ----
            # 端点卡在 4(NOTPRESENT)/2 而 PnP 在场时，写回 DeviceState=1 是合法的自愈；
            # 底层不在场时写 1 会造出点不动的僵尸端点 —— 所以必须先判 pnp_present。
            if visible and allow_repair:
                cur = fresh.get(e["devid"]) or {}
                fixable = (why.startswith("not-in-active-list")
                           or why.startswith("zombie-endpoint")
                           or why == "endpoint-gone")
                if fixable and cur.get("pnp_present") and not cur.get("pnp_problem"):
                    if write_devicestate(cur, STATE_ACTIVE):
                        time.sleep(0.3)
                        active = api.active_ids()
                        fresh = dict((x["devid"], x) for x in targets_classified())
                        ok2, why2, _ = _check(e, visible, active, fresh, api)
                        if ok2:
                            continue
                        why = why2 or why
            problems.append("%s:%s" % (e["name"] or e["guid"][:8], why))

        if not problems:
            return True, "ok(%d calls)" % calls

        # ---- 隐藏失败 -> 立即回滚成可见（安全方向）----
        if not visible:
            log("隐藏未达标（%s），立即回滚为可用" % "; ".join(problems))
            _force_visible(need, api)
            return False, ";".join(problems)

        return False, ";".join(problems)
    finally:
        api.close()


def _force_visible(eps, api=None):
    """无条件把端点拉回可见 —— 回滚/救援用，不追求优雅。"""
    own = api is None
    api = api or AudioAPI()
    try:
        pc = _get_policy()
        for e in eps:
            if not e.get("pnp_present") or e.get("pnp_problem"):
                continue
            try:
                pc.set_visible(e["devid"], True)
            except Exception:
                pass
            st = e.get("state")
            if st in (STATE_NOTPRESENT, STATE_UNPLUGGED):
                write_devicestate(e, STATE_ACTIVE)
        time.sleep(0.2)
    finally:
        if own:
            api.close()


def restore_all():
    """故障安全：把 HECATE 端点全部恢复成"可用"。
    退出、熔断、用户手动救援都走这里。
    """
    eps = targets_classified()
    if not eps:
        return False, "no-target-endpoint"
    ok, detail = apply_visible(True, reason="restore")
    if ok:
        return True, detail
    # 兜底：直接写（只对在场端点）
    _force_visible([e for e in eps if e.get("operable")])
    st = current_state()
    return st == "visible", "%s (state=%s)" % (detail, st)


# ================= 熔断 =================
def fuse_on(reason):
    try:
        with open(FUSE_FILE, "w", encoding="utf-8") as f:
            json.dump({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "reason": reason}, f, ensure_ascii=False)
    except Exception:
        pass
    log("=" * 50)
    log("★ 连续失败，已熔断（%s）" % reason)
    log("  程序转入只观察模式，不再改动任何设备。")
    log("  排查后执行  python guard.py --reset-fuse  解除。")
    log("=" * 50)


def fuse_present():
    return os.path.isfile(FUSE_FILE)


def fuse_off():
    try:
        if os.path.isfile(FUSE_FILE):
            os.remove(FUSE_FILE)
            return True
    except Exception:
        pass
    return False


# ================= 守护 =================
class Guard:
    def __init__(self, cfg):
        self.cfg = cfg
        self.mode = cfg["mode"]
        self.reader = BatteryReader()
        self.batt = None
        self.batt_ts = 0.0
        self.last_apply = None        # 'online' / 'offline'
        self.last_verify = 0.0
        self.cand = None
        self.cand_n = 0
        self.cand_since = 0.0
        self.online_bit = None
        self.last_change_ts = time.time()
        self.fail_n = 0
        self.next_retry = 0.0
        self.passive_until = 0.0
        self.hb_ts = 0.0
        self._closed = False
        self._logged = {}

    # ---------- 判定 ----------
    def judge(self):
        now = time.time()
        b, raw = self.reader.read()
        bit = self.reader.online_bit(raw)
        self.online_bit = bit

        if b is not None and b >= 0:
            self.batt = b
            self.batt_ts = now
            # 可选：把"在线标志位"也作为上线依据（更贴近链路状态，但更激进）
            if self.cfg.get("use_online_bit") and b == 0 and bit == 1:
                self.log_throttled("bitup", "在线标志位=1 但电量为 0，按在线处理"
                                           "（use_online_bit=true）", 60)
                return "online"
            # 两路信号不一致时记一笔（数据质量信号，便于排查）
            if b > 0 and bit == 0:
                self.log_throttled("battbit", "电量=%d%% 但在线标志位=0（不一致）" % b, 300)
            return "online" if b > 0 else "offline"
        if b == -1:
            self.batt = 0
            self.batt_ts = now
            return "offline"

        if self.reader.mode != "hidlib":
            self.reader._init()
        if self.batt is None:
            return "unknown"
        if (now - self.batt_ts) > BATT_TIMEOUT:
            return "unknown"
        return "online" if self.batt > 0 else "offline"

    def safe_to_hide(self):
        """隐藏前的最后一道闸：当场再读一次电量。
        v1 是"判定为离线就隐藏"，判定与执行之间隔着一段防抖时间，
        这期间用户可能刚把耳机戴上了 —— 于是"连上了也被屏蔽"。
        """
        b, raw = self.reader.read()
        if b is None:
            return False, "读数丢失"
        if b > 0:
            return False, "电量 %d%%（耳机已上线）" % b
        bit = self.reader.online_bit(raw)
        if bit == 1:
            return False, "在线标志位=1"
        return True, "ok"

    def log_throttled(self, key, msg, every=300):
        now = time.time()
        if now - self._logged.get(key, 0) >= every:
            self._logged[key] = now
            log(msg)

    def heartbeat(self):
        now = time.time()
        if now - self.hb_ts < HEARTBEAT_EVERY:
            return
        self.hb_ts = now
        try:
            with open(HB_FILE, "w", encoding="utf-8") as f:
                f.write("%.1f\n" % now)
        except Exception:
            pass

    # ---------- 是否需要动作 ----------
    def deviations(self, want_visible):
        """真实状态与期望是否一致（不信缓存，每次重新读）"""
        eps = targets_classified()
        live = [e for e in eps if e["operable"] and not e["pnp_problem"]]
        if not live:
            return "absent", None
        if want_visible:
            bad = [e for e in live if e["state"] != STATE_ACTIVE]
            if bad:
                return "not-visible", bad
            return "visible", None
        bad = [e for e in live if e["state"] == STATE_ACTIVE]
        if bad:
            return "still-active", bad
        return "hidden", None

    def apply(self, verdict):
        if verdict == "unknown":
            return

        want_visible = (verdict == "online")

        # PnP 层有问题 -> 完全不动，等硬件恢复
        if time.time() < self.passive_until:
            return
        eps = targets_classified()
        pnp_bad = [e for e in eps if e["pnp_problem"]]
        if pnp_bad and not any(e["operable"] and not e["pnp_problem"] for e in eps):
            codes = sorted(set(e["pnp_problem"] for e in pnp_bad))
            self.log_throttled("pnp", "底层设备异常（问题码 %s），暂停端点操作；"
                                      "这需要管理员启用设备，本程序不会去动它"
                              % ",".join(str(c) for c in codes), 600)
            self.passive_until = time.time() + 60
            return

        # 隐藏前二次确认
        if not want_visible:
            ok, why = self.safe_to_hide()
            if not ok:
                self.log_throttled("hide-abort", "放弃隐藏：%s" % why, 30)
                return

        if self.mode == "monitor":
            self.log_throttled("monitor", "【只观察】判定=%s，实际=%s（未操作）"
                              % (verdict, self.deviations(want_visible)[0]), 60)
            self.last_apply = verdict
            self.last_verify = time.time()
            return

        t0 = time.time()
        ok, detail = apply_visible(want_visible, reason=verdict)
        ms = (time.time() - t0) * 1000
        self.last_verify = time.time()

        if ok:
            self.last_apply = verdict
            self.fail_n = 0
            self.next_retry = 0.0
            log("切换 -> %s 完成（%.0f ms，%s）"
                % ("可用" if want_visible else "隐藏", ms, detail))
        else:
            # ★ 失败绝不能记为"已生效" —— v1 的假成功就是这么进死锁的。
            #   这里只退避重试，last_apply 保持不动。
            self.fail_n += 1
            delay = min(5 * (2 ** (self.fail_n - 1)), 120)
            self.next_retry = time.time() + delay
            log("切换 -> %s 失败（%.0f ms）：%s；%.0fs 后重试（第 %d 次）"
                % ("可用" if want_visible else "隐藏", ms, detail,
                   delay, self.fail_n))
            if self.fail_n >= self.cfg["max_apply_failures"]:
                try:
                    restore_all()
                except Exception:
                    pass
                fuse_on(detail)
                self.mode = "monitor"
                self.fail_n = 0
                self.next_retry = 0.0

    # ---------- 主循环 ----------
    def run(self):
        cfg = self.cfg
        log("=" * 54)
        log("Edifier Guard v%s 启动 | 模式=%s | 管理员=%s | HID=%s"
            % (VERSION, self.mode, is_admin(), self.reader.mode))
        log("轮询 %.1fs（稳定 %.0fs 后转 %.1fs）| 离线确认 %.1fs | 巡检 %.0fs | 熔断阈值 %d 次"
            % (cfg["poll_interval"], cfg.get("idle_after_secs", 25.0),
               cfg.get("poll_interval_idle", 1.2), cfg["offline_confirm_secs"],
               VERIFY_EVERY, cfg["max_apply_failures"]))
        n = len([e for e in targets_classified() if e["operable"]])
        log("在场目标端点 %d 个" % n)
        if fuse_present():
            log("★ 检测到熔断标记 guard.fused —— 本次只观察，不操作设备")
        log("=" * 54)

        # 启动自愈：先无条件恢复可用，再由下面的判定决定是否隐藏
        # ★ 只观察模式下不做任何写操作 —— 否则用户手动隐藏的设备会被程序改回来
        try:
            st = current_state()
            if self.mode == "monitor":
                log("只观察模式：启动时不改动任何设备（当前 %s）" % st)
            elif st in ("hidden", "mixed"):
                log("启动自愈：端点处于 %s，先恢复为可用…" % st)
                ok, d = restore_all()
                log("自愈%s（%s）" % ("成功" if ok else "未完全成功", d))
        except Exception as e:
            log("自愈异常（不影响后续）: %s" % e)

        # 宽限：等第一份电量
        t0 = time.time()
        while time.time() - t0 < GRACE_ON_BOOT:
            if self.reader.read()[0] is not None:
                break
            time.sleep(0.2)

        while True:
            try:
                self.heartbeat()
                v = self.judge()
                now = time.time()

                if v != self.cand:
                    self.cand = v
                    self.cand_n = 1
                    self.cand_since = now
                    self.last_change_ts = now      # 有变化 -> 重新进入快速轮询窗口
                elif self.cand_n < 100000:
                    self.cand_n += 1

                want_visible = (v == "online")

                # ---------- 1) 判定是否该动作 ----------
                should = False
                if v == "online":
                    should = (self.last_apply != "online")
                elif v == "offline":
                    # 非对称防抖：上线立即，下线要持续确认一段时间
                    should = (self.last_apply != "offline"
                              and (now - self.cand_since) >= cfg["offline_confirm_secs"])
                # unknown -> 什么都不做（保持现状是安全方向）

                # ---------- 2) 定期巡检：即使"自认为成功"也要重新核实 ----------
                # ★ v1 的死锁就出在这里：只比缓存不校验，一旦自认为成功就再也不管了。
                if (not should and self.last_apply is not None
                        and now - self.last_verify >= VERIFY_EVERY):
                    self.last_verify = now
                    dev, _bad = self.deviations(self.last_apply == "online")
                    if dev in ("not-visible", "still-active"):
                        log("巡检发现状态偏离（期望=%s 实际=%s），重新执行"
                            % ("可见" if self.last_apply == "online" else "隐藏", dev))
                        should = True

                # ---------- 3) 执行（带退避） ----------
                if should and now >= self.next_retry:
                    self.apply(v if v != "unknown" else self.cand)

                if "--debug" in sys.argv:
                    log("状态=%s 电量=%s 稳定=%d 期望=%s 实际=%s 熔断=%s"
                        % (v, self.batt, self.cand_n,
                           "可见" if want_visible else "隐藏",
                           current_state(), "是" if fuse_present() else "否"))

                # ★ 自适应轮询：刚发生变化时用快速间隔保证响应，
                #   长时间稳定后自动放慢，降低唤醒频率（省电）。
                stable_for = now - self.last_change_ts
                if stable_for >= float(cfg.get("idle_after_secs", 25.0)):
                    interval = float(cfg.get("poll_interval_idle", 1.2))
                else:
                    interval = float(cfg["poll_interval"])
                time.sleep(max(0.1, interval))
            except KeyboardInterrupt:
                break
            except Exception as e:
                log("循环异常: %r" % (e,))
                time.sleep(1.0)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self.mode == "monitor":
                log("只观察模式退出，不改动设备")
                try:
                    self.reader.close()
                except Exception:
                    pass
                return
            log("正在退出，恢复端点可用状态…")
            try:
                ok, d = restore_all()
                log("恢复%s（%s）" % ("成功" if ok else "失败", d))
            except Exception as e:
                log("恢复失败: %s" % e)
            try:
                self.reader.close()
            except Exception:
                pass
            log("已退出")
        finally:
            # 无论走哪条路径退出，都清理自己的 PID 文件
            _remove_pid()


# ================= 体检 =================
def doctor():
    """一次性把问题说清楚。返回 0=健康，1=有问题。"""
    problems = []
    print("=" * 68)
    print("  HECATE 音频设备体检    guard v%s   管理员=%s"
          % (VERSION, "是" if is_admin() else "否"))
    print("=" * 68)

    # 1) PnP 层
    print("\n[1/4] PnP 设备层（最根本）")
    eps = targets_classified()
    if not eps:
        print("  ✗ 系统里找不到任何 HECATE 音频端点")
        problems.append("no-endpoint")
    seen = set()
    for e in eps:
        inst = strip_prefix(e.get("devinst"))
        if inst in seen or not inst:
            continue
        seen.add(inst)
        if e["pnp_problem"] is None:
            print("  - %s" % inst)
            print("      不在场（接收器未插 / 耳机未连）")
        elif e["pnp_problem"] == 0:
            print("  ✓ %s" % inst)
            print("      正常运行")
        else:
            print("  ✗ %s" % inst)
            print("      问题码 %d：%s" % (e["pnp_problem"],
                                        _PROBLEM_NAME.get(e["pnp_problem"], "未知")))
            problems.append("pnp-%d" % e["pnp_problem"])

    # 2) 端点状态
    print("\n[2/4] 音频端点状态（注册表）")
    for e in eps:
        mark = "✓" if e["state"] == STATE_ACTIVE else "·"
        print("  %s [%s] %-30s %s" % (mark, e["kind"], (e["name"] or "<空>")[:30],
                                      state_name(e["state"])))
    st = current_state(eps)
    print("  → 汇总：%s" % st)
    if st == "mixed":
        problems.append("mixed-state")

    # 判定"按设计隐藏"：所有在场端点都是 DISABLED，且守护正在运行（心跳新鲜）。
    # 这时"不在活跃列表"是正常现象（耳机离线被主动隐藏），不该报成故障。
    hb_age = None
    try:
        with open(HB_FILE, "r", encoding="utf-8") as f:
            hb_age = time.time() - float(f.read().strip())
    except Exception:
        hb_age = None
    guard_running = hb_age is not None and hb_age < 30
    expected_hidden = (st == "hidden") and guard_running

    # 3) 音频 API（权威）
    print("\n[3/4] Windows 音频 API（声音设置里到底能不能用）")
    api = AudioAPI()
    try:
        if not api.ok:
            print("  ✗ 无法创建 IMMDeviceEnumerator")
            problems.append("api-unavailable")
        else:
            act = api.active_ids()
            print("  活跃端点：输出 %d 个 / 输入 %d 个"
                  % (len(act[0]), len(act[1])))
            for e in eps:
                if not e["operable"]:
                    continue
                in_active = e["devid"].lower() in act[_flow_of(e)]
                hr = api.activate_hr(e["devid"])
                if not in_active:
                    if expected_hidden and e["state"] == STATE_DISABLED:
                        print("  ✓ [%s] %s 已按设计隐藏（耳机离线，守护运行中）→ 正常"
                              % (e["kind"], e["name"]))
                    else:
                        print("  ✗ [%s] %s 不在活跃列表 → 声音设置里看不到"
                              % (e["kind"], e["name"]))
                        problems.append("not-in-active:" + e["kind"])
                elif hr in (HR_PATH_NOT_FOUND, HR_NOT_FOUND):
                    print("  ✗ [%s] %s 在列表里但音频链路不通（0x%08X）"
                          % (e["kind"], e["name"], hr))
                    print("      = 僵尸端点：多半是底层 USB 音频接口被禁用")
                    problems.append("zombie:" + e["kind"])
                else:
                    print("  ✓ [%s] %s 可见且可用（Activate=0x%08X）"
                          % (e["kind"], e["name"], hr or 0))
    finally:
        api.close()

    # 4) 程序自身状态
    print("\n[4/4] 程序状态")
    print("  熔断标记 : %s" % ("★ 已熔断" if fuse_present() else "无"))
    try:
        with open(HB_FILE, "r", encoding="utf-8") as f:
            age = time.time() - float(f.read().strip())
        print("  心跳     : %.0f 秒前%s" % (age, "（可能在运行）" if age < 30 else "（未运行）"))
    except Exception:
        print("  心跳     : 无（未运行）")
    print("  配置文件 : %s" % (CONFIG_FILE if os.path.isfile(CONFIG_FILE) else "未创建（用默认值）"))

    print("\n" + "=" * 68)
    if not problems:
        print("  结论：一切正常 ✓")
        return 0
    print("  结论：发现 %d 类问题" % len(problems))
    for p in problems:
        if p.startswith("pnp-22"):
            print("    · 设备被禁用 → 双击 FIX_AUDIO_DEVICE.bat（需管理员）")
        elif p.startswith("pnp-"):
            print("    · 设备驱动异常 → 设备管理器里查看该设备，必要时重装驱动")
        elif p.startswith("zombie"):
            print("    · 僵尸端点 → 检查底层 USB 音频接口是否被禁用")
        elif p.startswith("not-in-active"):
            print("    · 端点未激活 → python guard.py --restore")
        elif p == "mixed-state":
            print("    · 端点状态不一致 → python guard.py --restore")
    print("=" * 68)
    return 1


# ================= CLI =================
def print_status(cfg):
    eps = targets_classified()
    st = current_state(eps)
    print("Edifier Guard v%s" % VERSION)
    print("  管理员权限 :", "是" if is_admin() else "否")
    print("  生效模式   :", "只观察（monitor）" if (cfg["mode"] == "monitor" or fuse_present())
          else "enforce")
    print("  熔断       :", "★ 已熔断" if fuse_present() else "无")
    print("  设备状态   :", st)
    print("  音频 API   :", "可用" if AudioAPI().ok else "不可用")
    print("  IPolicyCfg :", "可用" if _get_policy().ok else "不可用")
    print("  端点：")
    for e in eps:
        tag = ("可用" if e["usable"] else
               ("在场" if e["operable"] else "不在场"))
        pnp = "" if not e["pnp_problem"] else "  问题码=%d" % e["pnp_problem"]
        print("    [%s][%s] %-30s state=%-12s%s"
              % (e["kind"], tag, (e["name"] or "<空>")[:30],
                 state_name(e["state"]), pnp))


def print_list():
    eps = targets_classified()
    print("HECATE 音频端点：")
    for e in eps:
        tag = ("可用" if e["usable"] else
               ("在场" if e["operable"] else "不在场"))
        print("  [%s][%s] %-30s state=%-12s %s"
              % (e["kind"], tag, (e["name"] or "<空>")[:30],
                 state_name(e["state"]), e["devid"]))
    print("\n汇总:", current_state(eps))


def run_once(cfg):
    r = BatteryReader()
    b = raw = None
    t0 = time.time()
    while time.time() - t0 < 3:
        bb, rr = r.read()
        if bb is not None:
            b, raw = bb, rr
        if b is not None and r.mac:
            break
        time.sleep(0.2)
    print("HID 模式      :", r.mode)
    print("电量          :", b if b is not None else "读不到")
    print("在线标志位    :", r.online_bit(raw))
    print("对端MAC(op07) :", r.mac if r.mac else "（未出现）")
    print("原始报文      :", raw.hex() if raw else "无")
    print("判定          :", "在线" if (b or 0) > 0 else
          ("离线" if b is not None else "未知"))
    print("设备状态      :", current_state())
    r.close()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    cfg = load_config()
    save_default_config()

    if "--doctor" in sys.argv:
        sys.exit(doctor())

    if "--reset-fuse" in sys.argv:
        print("解除熔断:", "成功" if fuse_off() else "本来就没有熔断")
        return

    if "--restore" in sys.argv:
        print("正在恢复 HECATE 音频端点为「可用」…")
        ok, detail = restore_all()
        print("  结果:", "成功" if ok else "失败（%s）" % detail)
        print("  当前状态:", current_state())
        return

    if "--list" in sys.argv:
        print_list()
        return

    if "--status" in sys.argv:
        print_status(cfg)
        return

    if "--once" in sys.argv:
        run_once(cfg)
        return

    # 守护模式
    if "--monitor" in sys.argv:
        cfg["mode"] = "monitor"
    if "--enforce" in sys.argv:
        cfg["mode"] = "enforce"

    # ★ 熔断标记存在 = 曾经连续失败过。必须强制只观察，
    #   直到用户排查后执行 --reset-fuse。
    if fuse_present():
        cfg["mode"] = "monitor"

    if not acquire_single_instance():
        log("已有另一个 Guard 实例在运行（或权限更高），本实例退出")
        return

    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    g = Guard(cfg)
    atexit.register(g.shutdown)

    def _sig(signum, frame):
        raise KeyboardInterrupt()

    for s in ("SIGTERM", "SIGINT", "SIGBREAK"):
        try:
            signal.signal(getattr(signal, s), _sig)
        except Exception:
            pass

    try:
        g.run()
    except KeyboardInterrupt:
        pass
    finally:
        g.shutdown()


if __name__ == "__main__":
    main()
