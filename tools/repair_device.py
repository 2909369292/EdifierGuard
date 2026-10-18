# -*- coding: utf-8 -*-
"""
HECATE 音频端点修复工具（repair_device.py）

用途
----
当「HECATE G3 Max」在 Windows 声音设置里消失（明明插着接收器、耳机也有电），
本工具把它的音频端点状态修正回来。**不需要管理员权限。**

背景
----
Windows 把音频端点状态存在注册表：
    HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\MMDevices\\Audio\\{Render|Capture}\\{GUID}\\DeviceState
取值为位标志（注意：与 mmdeviceapi.h 的 DEVICE_STATE_* 不是同一套）：
    1           = ACTIVE       正常可用
    2           = UNPLUGGED    已拔出
    4           = NOTPRESENT   未接入（声音设置里看不到）
    0x10000001  = DISABLED     被用户禁用

「端点卡在 4 但硬件其实在场」是一种常见故障：底层的 USB 音频接口还在
（SetupAPI 能枚举到），但端点被标记成 NOTPRESENT，于是声音设置里就没有它。

本工具的做法
------------
1. 用 SetupAPI 取「当前在场的 PnP 设备实例」集合
2. 遍历所有 HECATE 端点，把 devinst 去掉 `{N}.` 前缀后与上面比对
3. 在场 -> DeviceState = 1(ACTIVE)；不在场 -> DeviceState = 4(NOTPRESENT)
   （不在场的保持 4 是对的，设成 1 会造出点不动的幽灵设备）
4. 用 IMMDeviceEnumerator（Windows 自己的音频 API）验证端点是否真的出现在
   活跃列表里 —— 这才是「声音设置里能不能看到」的权威答案

用法
----
    python repair_device.py            仅诊断，不改任何东西
    python repair_device.py --apply    实际修复
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time
import winreg

APPLY = "--apply" in sys.argv

STATE_ACTIVE = 1
STATE_UNPLUGGED = 2
STATE_NOTPRESENT = 4
STATE_DISABLED = 0x10000001

STATE_NAME = {
    1: "ACTIVE 可用",
    2: "UNPLUGGED 已拔出",
    4: "NOTPRESENT 未接入",
    0x10000001: "DISABLED 已禁用",
}

PKEY_NAME = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"
PKEY_DEVINST = "{b3f8fa53-0004-438e-9003-51a46e139bfc},2"

BASES = (
    ("Render", r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"),
    ("Capture", r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"),
)


# ---------------- 在场设备集合 ----------------
def present_instances():
    """返回当前在场（PRESENT）的 PnP 设备实例 ID 集合（大写）"""
    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wt.LPCWSTR,
                                             wt.HWND, wt.DWORD]
    setupapi.SetupDiEnumDeviceInfo.argtypes = [wt.HANDLE, wt.DWORD,
                                              ctypes.c_void_p]
    setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                                     wt.LPWSTR, wt.DWORD,
                                                     ctypes.POINTER(wt.DWORD)]
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

    class SP_DEVINFO_DATA(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD),
                    ("ClassGuid", ctypes.c_ubyte * 16),
                    ("DevInst", wt.DWORD),
                    ("Reserved", ctypes.c_void_p)]

    # ★ 必须用 ALLCLASSES|PRESENT(0x06)，只用 PRESENT(0x02) 会枚举到 0 个
    h = setupapi.SetupDiGetClassDevsW(None, None, None, 0x06)
    out = set()
    if not h:
        return out
    i = 0
    while True:
        d = SP_DEVINFO_DATA()
        d.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
        if not setupapi.SetupDiEnumDeviceInfo(h, i, ctypes.byref(d)):
            break
        b = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD()
        if setupapi.SetupDiGetDeviceInstanceIdW(h, ctypes.byref(d), b, 1024,
                                                ctypes.byref(n)):
            out.add(b.value.upper())
        i += 1
    setupapi.SetupDiDestroyDeviceInfoList(ctypes.c_void_p(h))
    return out


def strip_prefix(devinst):
    """去掉注册表 devinst 里的 `{N}.` 前缀，得到 PnP 实例 ID"""
    s = (devinst or "").upper()
    if s.startswith("{") and "}." in s:
        s = s.split("}.", 1)[1]
    return s


def read_value(base, guid, name="DeviceState"):
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + "\\" + guid, 0,
                           winreg.KEY_READ)
        try:
            v, _ = winreg.QueryValueEx(k, name)
            return v
        finally:
            winreg.CloseKey(k)
    except Exception:
        return None


def iter_endpoints():
    """遍历所有音频端点，产出 (kind, base, guid, name, devinst, state)"""
    for kind, base in BASES:
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base, 0,
                               winreg.KEY_READ)
        except Exception:
            continue
        try:
            cnt = winreg.QueryInfoKey(k)[0]
            for i in range(cnt):
                guid = winreg.EnumKey(k, i)
                nm = di = None
                try:
                    pk = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                        base + "\\" + guid + "\\Properties",
                                        0, winreg.KEY_READ)
                    try:
                        nm, _ = winreg.QueryValueEx(pk, PKEY_NAME)
                    except Exception:
                        pass
                    try:
                        di, _ = winreg.QueryValueEx(pk, PKEY_DEVINST)
                    except Exception:
                        pass
                    winreg.CloseKey(pk)
                except Exception:
                    pass
                yield kind, base, guid, nm, di, read_value(base, guid)
        finally:
            winreg.CloseKey(k)


def is_hecare(nm, di):
    n = (nm or "").upper()
    d = (di or "").upper()
    return ("HECATE" in n) or ("VID_35BB" in d)


# ---------------- 权威验证：Windows 音频 API ----------------
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD),
                ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


def _g(a, b, c, d):
    return _GUID(a, b, c, (ctypes.c_ubyte * 8)(*d))


_CLSID_MMDE = _g(0xBCDE0395, 0xE52F, 0x467C,
                 (0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E))
_IID_IMMDE = _g(0xA95664D2, 0x9614, 0x4F35,
                (0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6))
_VTP = ctypes.POINTER(ctypes.c_void_p)


def _vfn(ptr, idx, *argtypes):
    """取 COM 接口第 idx 个虚函数（this 指针显式作为第一个参数传）"""
    vt = ctypes.cast(ptr, _VTP)[0]
    faddr = ctypes.cast(vt, _VTP)[idx]
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(faddr)


def active_endpoint_ids():
    """用 IMMDeviceEnumerator 列出「活跃」端点 ID。
    这是声音设置里能看到哪些设备的权威来源。
    返回 {flow: set(小写端点ID)}，flow 0=Render 1=Capture
    """
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitialize.restype = ctypes.c_long
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    result = {0: set(), 1: set()}
    try:
        ole32.CoInitialize(None)
        en = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(_CLSID_MMDE), None, 1,
                                    ctypes.byref(_IID_IMMDE),
                                    ctypes.byref(en))
        if hr != 0 or not en.value:
            return result
        en_p = ctypes.c_void_p(en.value)
        for flow in (0, 1):
            coll = ctypes.c_void_p()
            # IMMDeviceEnumerator vtable[3] = EnumAudioEndpoints
            _vfn(en_p.value, 3, wt.DWORD, wt.DWORD,
                 ctypes.POINTER(ctypes.c_void_p))(
                en_p, flow, 1, ctypes.byref(coll))       # 1 = DEVICE_STATE_ACTIVE
            if not coll.value:
                continue
            cnt = ctypes.c_uint(0)
            # IMMDeviceCollection vtable[3] = GetCount
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
                    result[flow].add(ctypes.wstring_at(sid.value).lower())
                    ole32.CoTaskMemFree(sid)
                _vfn(dev.value, 2)(ctypes.c_void_p(dev.value))   # Release
            _vfn(coll.value, 2)(ctypes.c_void_p(coll.value))
        _vfn(en_p.value, 2)(en_p)
    except Exception:
        pass
    finally:
        try:
            ole32.CoUninitialize()
        except Exception:
            pass
    return result


# ---------------- ★ PnP 设备启用状态（最根本的一层，优先检查） ----------------
PROBLEM_NAME = {
    0: "正常运行", 1: "未配置", 3: "驱动损坏", 10: "无法启动", 12: "资源不足",
    14: "需要重启", 18: "需要重装驱动", 19: "注册表损坏", 21: "正在移除",
    22: "★ 已被禁用（需要管理员权限启用）", 24: "设备未连接", 28: "驱动未安装",
    31: "驱动加载失败", 43: "已被停止", 45: "设备当前未连接",
}


def pnp_problems():
    """扫描全部在场设备，返回 [(实例ID, 问题代码, status)]，只含异常项。

    ★ 这是排查「端点在场却完全用不了」的关键一层。
      设备是可以被"禁用"的（问题代码 22 = CM_PROB_DISABLED / ConfigFlags 0x1）。
      一旦底层 USB 音频接口被禁用，就会出现下面这种极具迷惑性的组合：
        - 接收器插着、HID 正常、电量读得到   （所以你以为硬件没问题）
        - 音频端点注册表 DeviceState 仍是 1  （所以声音设置里"看得见"）
        - 但 KS 音频链路根本没建立
        - IAudioEndpointVolume 激活失败 0x80070003 (ERROR_PATH_NOT_FOUND)
        - 症状：选得中但没声音、音量调完自动弹回 0、厂商软件连不上
      这种情况下改注册表 DeviceState 毫无作用，必须启用设备本身。
    """
    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    cfgapi = ctypes.WinDLL("cfgmgr32", use_last_error=True)
    setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wt.LPCWSTR, wt.HWND, wt.DWORD]
    setupapi.SetupDiEnumDeviceInfo.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p]
    setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                                     wt.LPWSTR, wt.DWORD,
                                                     ctypes.POINTER(wt.DWORD)]
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    cfgapi.CM_Get_DevNode_Status.argtypes = [ctypes.POINTER(wt.ULONG),
                                             ctypes.POINTER(wt.ULONG),
                                             wt.DWORD, wt.ULONG]
    cfgapi.CM_Get_DevNode_Status.restype = wt.ULONG

    class SP_DEVINFO_DATA(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("ClassGuid", ctypes.c_ubyte * 16),
                    ("DevInst", wt.DWORD), ("Reserved", ctypes.c_void_p)]

    out = []
    h = setupapi.SetupDiGetClassDevsW(None, None, None, 0x04 | 0x02)  # ALLCLASSES|PRESENT
    if not h:
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
            if cfgapi.CM_Get_DevNode_Status(ctypes.byref(st), ctypes.byref(pb),
                                            d.DevInst, 0) == 0 and pb.value != 0:
                out.append((b.value, pb.value, st.value))
        i += 1
    setupapi.SetupDiDestroyDeviceInfoList(ctypes.c_void_p(h))
    return out


def check_pnp_disabled():
    """若 HECATE 底层设备被禁用/异常，醒目提示。返回 True 表示存在该类问题。"""
    bad = [(x, p, s) for (x, p, s) in pnp_problems() if "35BB" in x.upper()]
    if not bad:
        return False
    print("=" * 70)
    print("  ★★ 发现被禁用 / 异常的设备（这才是最根本的原因）")
    print("=" * 70)
    for inst, pb, st in bad:
        print("  [问题 %d · %s]" % (pb, PROBLEM_NAME.get(pb, "未知")))
        print("      %s" % inst)
    print()
    print("  >>> 必须先以管理员权限「启用」该设备。")
    print("  >>> 只改注册表 DeviceState 是治不好的（会造出点不动的幽灵端点）。")
    print("  >>> 请双击运行同目录下的  FIX_AUDIO_DEVICE.bat（会自动请求管理员权限）")
    print()
    return True


# ---------------- 主流程 ----------------
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("=" * 70)
    print("  HECATE 音频端点修复工具  模式: %s"
          % ("★ 实际修复 (--apply)" if APPLY else "仅诊断（加 --apply 才修改）"))
    print("=" * 70)

    # ★ 第一层：PnP 设备是否被禁用（最根本的原因，优先检查）
    try:
        check_pnp_disabled()
    except Exception as e:
        print("(PnP 状态检查跳过: %s)\n" % e)

    present = present_instances()
    print("当前在场的 PnP 设备实例数: %d" % len(present))
    print()

    issues = []
    plan = []
    print("%-9s %-34s %-11s %-10s %s" % ("类型", "名称", "端点状态", "底层设备", "需要的值"))
    print("-" * 70)
    for kind, base, guid, nm, di, st in iter_endpoints():
        if not is_hecare(nm, di):
            continue
        if "Hands-Free" in (nm or ""):
            continue
        inst = strip_prefix(di)
        is_present = inst in present
        want = STATE_ACTIVE if is_present else STATE_NOTPRESENT
        need_fix = (st != want)
        print("%-9s %-34s %-11s %-10s %s%s"
              % (kind, (nm or "")[:34],
                 STATE_NAME.get(st, str(st)).split()[0],
                 "在场" if is_present else "不在场",
                 STATE_NAME.get(want, str(want)).split()[0],
                 "   ← 需修正" if need_fix else ""))
        if need_fix:
            issues.append((kind, nm, st, want))
            plan.append((kind, base, guid, want))

    print()
    if not issues:
        print("所有 HECATE 端点状态都正确，无需修复。")
    else:
        print("发现 %d 个端点状态不正确：" % len(issues))
        for kind, nm, st, want in issues:
            print("  [%s] %-34s %s -> %s"
                  % (kind, (nm or "")[:34],
                     STATE_NAME.get(st, str(st)), STATE_NAME.get(want, str(want))))

    if APPLY and plan:
        print()
        print("正在写入注册表…")
        ok = 0
        for kind, base, guid, want in plan:
            try:
                k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                   base + "\\" + guid, 0, winreg.KEY_SET_VALUE)
                winreg.SetValueEx(k, "DeviceState", 0, winreg.REG_DWORD, want)
                winreg.CloseKey(k)
                ok += 1
                print("  ✓ [%s] %s -> %d" % (kind, guid[:16], want))
            except Exception as e:
                print("  ✗ [%s] %s 失败: %s" % (kind, guid[:16], e))
        print("  成功 %d/%d" % (ok, len(plan)))
        time.sleep(1.0)

    # ---- 权威验证 ----
    print()
    print("=" * 70)
    print("  验证：用 Windows 音频 API 列出「活跃」端点")
    print("  （这才是声音设置里实际能看到的内容）")
    print("=" * 70)
    act = active_endpoint_ids()
    for flow, label in ((0, "输出 Render"), (1, "输入 Capture")):
        ids = act[flow]
        print()
        print("  %s：活跃 %d 个" % (label, len(ids)))
        hec = [x for x in ids if "08e4e738" in x or "942304f1" in x
               or "6fd9f8cc" in x or "be3f9b41" in x]
        if hec:
            for x in sorted(hec):
                print("    ★ %s ✓ 出现在活跃列表" % x)
        else:
            print("    （未见 HECATE 端点）")

    print()
    if not plan:
        print("结论：端点状态本来就是对的。若声音设置里仍看不到设备，")
        print("      尝试拔插一次接收器，或重启 AudioEndpointBuilder 服务。")
    elif not APPLY:
        print("结论：以上是需要修正的项。加 --apply 参数执行修复。")
    else:
        print("结论：已修复。若上面验证显示了 ★ 行，说明设备已回到系统列表。")


if __name__ == "__main__":
    main()
