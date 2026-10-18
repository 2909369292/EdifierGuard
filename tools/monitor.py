# -*- coding: utf-8 -*-
"""
HECATE G3 Max 实时监视器（monitor.py）

双击 START_MONITOR.bat 运行，或直接 python monitor.py。

每 0.25 秒刷新一行，显示：
    时间 | 电量 | 在线位 | 设备状态 | 耗时 | 说明

测试方法：
  1. 保持窗口开着
  2. 从底座拿起耳机戴上（开机）
     -> 应在 0.5 秒内看到「设备状态 = enabled」
  3. 把耳机放回底座
     -> 应在 1 秒内看到「设备状态 = disabled」

按 Ctrl+C 退出。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bootstrap  # noqa: F401  # 把「项目根」加入 sys.path
import guard

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def guard_alive():
    """检查 guard 进程是否在跑（读 guard.pid + OpenProcess）"""
    try:
        with open(guard.PID_FILE, "r", encoding="utf-8") as f:
            pid = int(f.read().strip())
    except Exception:
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wt.HANDLE
        k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
        k.CloseHandle.argtypes = [wt.HANDLE]
        h = k.OpenProcess(0x1000, False, pid)      # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = wt.DWORD()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        if not ok:
            return False
        return code.value == 259                   # STILL_ACTIVE
    except Exception:
        return None


def main():
    print("=" * 78)
    print("  HECATE G3 Max 实时监视器")
    print("=" * 78)
    print("  拿起耳机  -> 设备应变 可见（visible）")
    print("  放回底座  -> 设备应变 隐藏（hidden）")
    print("  按 Ctrl+C 退出")
    print("=" * 78)
    print()

    cfg = guard.load_config()
    r = guard.BatteryReader()
    print("HID 模式: %s   可写入: %s   轮询间隔: %.2fs"
          % (r.mode, r.can_write, cfg["poll_interval"]))
    print()
    print("%-10s %-8s %-8s %-12s %-8s %s"
          % ("时间", "电量", "在线位", "设备状态", "耗时", "说明"))
    print("-" * 78)

    prev_dev = None
    prev_batt = None
    alive_shown = None
    try:
        while True:
            t0 = time.time()
            b, raw = r.read()
            bit = raw[5] if (raw and len(raw) > 5) else None
            dev = guard.current_state()
            ms = (time.time() - t0) * 1000

            note = ""
            if prev_dev is not None and dev != prev_dev:
                if dev == "visible":
                    note = "★ 设备已出现（可用）"
                elif dev == "hidden":
                    note = "★ 设备已隐藏（不可用）"
            elif prev_batt is not None and b is not None and b != prev_batt:
                note = "电量变化 %s -> %s" % (prev_batt, b)

            # 顺带报一下后台守护进程的存活
            if alive_shown is None:
                av = guard_alive()
                if av is not None:
                    note = (note + "  |  后台守护: 运行中" if av
                            else note + "  |  后台守护: 未运行")
                    alive_shown = True

            prev_dev = dev
            prev_batt = b

            print("%-10s %-8s %-8s %-12s %-8s %s"
                  % (time.strftime("%H:%M:%S"),
                     b if b is not None else "-",
                     bit if bit is not None else "-",
                     dev, "%.0fms" % ms, note), flush=True)
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        r.close()
        print("\n已退出")


if __name__ == "__main__":
    main()
