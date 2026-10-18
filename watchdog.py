# -*- coding: utf-8 -*-
"""
Edifier Guard 看门狗 v2（watchdog）

职责：保证 guard.py 一直在跑，并且**真的在干活**。

v1 的看门狗只判断"进程有没有退出"。这次事故暴露了两个盲区：
  1. 进程活着但卡死（等 COM、卡在 HID 读、被挂起）—— v1 永远不会重启它
  2. 高权限实例和普通实例同时存在，互斥体互相拦，看门狗疯狂重启
  3. 看门狗自己在 guard 秒退 3 次后直接退出 —— 之后没人管了

v2 的做法：
  - 心跳（guard.heartbeat，guard 每 2 秒写一次）超过 STALE_SECS 没更新 -> 判定卡死，杀掉重启
  - 秒退连续 N 次 -> 不再退出，转为低频"哨兵模式"（每 60s 探一次），
    这样 guard 以后恢复正常了还能被拉起来
  - 启动前先做一次 --restore？不做。guard 自己启动时会自愈，
    在这里做只会让设备闪一下又消失
  - 熔断（guard.fused）存在时仍然启动 guard（它自己会进只观察模式），
    但日志里明确写出来

用法（由 VBS 启动器调用，无需手动执行）：
    pythonw.exe watchdog.py
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "guard.py")
LOG = os.path.join(HERE, "watchdog.log")
PIDFILE = os.path.join(HERE, "watchdog.pid")
GUARD_PID = os.path.join(HERE, "guard.pid")
HB = os.path.join(HERE, "guard.heartbeat")
FUSE = os.path.join(HERE, "guard.fused")

RESTART_DELAY = 3.0
HEALTHY_RESET = 60.0
FAST_EXIT_THRESHOLD = 2.0
MAX_FAST_EXITS = 3
STALE_SECS = 180.0        # 心跳超过这么久没更新 -> 判定卡死
SENTINEL_DELAY = 60.0     # 连续秒退后转入哨兵模式的探测间隔

_CANDS = [
    os.path.join(HERE, "runtime", "pythonw.exe"),
    os.path.join(os.path.dirname(sys.executable), "pythonw.exe"),
    sys.executable,
]


def _log(msg):
    try:
        # 与 guard.log 一致：超过 512KB 就轮转，避免长期无限增长
        if os.path.exists(LOG) and os.path.getsize(LOG) > 512 * 1024:
            try:
                os.replace(LOG, LOG + ".old")
            except Exception:
                pass
        line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _python():
    for p in _CANDS:
        if p and os.path.isfile(p):
            return p
    return sys.executable


def _pid_alive(pid):
    """不依赖 psutil：OpenProcess + GetExitCodeProcess"""
    import ctypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not k.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == 259               # STILL_ACTIVE
    finally:
        k.CloseHandle(h)


def _kill(pid):
    import ctypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k.OpenProcess(0x0001, False, pid)      # PROCESS_TERMINATE
    if not h:
        return False
    try:
        return bool(k.TerminateProcess(h, 0))
    finally:
        k.CloseHandle(h)


def _read_pid():
    try:
        with open(GUARD_PID, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return 0


def _hb_age():
    try:
        with open(HB, "r", encoding="utf-8") as f:
            return time.time() - float(f.read().strip())
    except Exception:
        return None


def _proc_image(pid):
    """取进程映像路径（小写）；查不到返回空串。
    杀掉前用它确认目标确实是 python —— 防止 guard.pid 陈旧、PID 被复用后误杀无关进程。"""
    import ctypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    k.QueryFullProcessImageNameW.restype = ctypes.c_int
    h = k.OpenProcess(0x1000, False, pid)     # QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.lower()
        return ""
    except Exception:
        return ""
    finally:
        k.CloseHandle(h)


def check_hang():
    """guard 进程活着但心跳停了 -> 杀掉，交给外层重启"""
    pid = _read_pid()
    if not pid or not _pid_alive(pid):
        return False
    img = _proc_image(pid)
    if img and "python" not in img:
        # guard.pid 是陈旧残留，这个 PID 已被别的进程复用 —— 绝不能杀
        _log("忽略陈旧 guard.pid=%d（现指向 %s，非 python），不误杀"
             % (pid, img))
        return False
    age = _hb_age()
    if age is None or age > STALE_SECS:
        _log("★ guard(pid=%d) 心跳已停 %s，判定卡死，强制重启"
             % (pid, "未知" if age is None else "%.0f 秒" % age))
        _kill(pid)
        return True
    return False


def main():
    if not os.path.isfile(GUARD):
        _log("找不到 guard.py: %s" % GUARD)
        return 1

    py = _python()
    _log("看门狗启动 | python=%s | pid=%d%s"
         % (py, os.getpid(), " | ★ 已熔断（只观察）" if os.path.isfile(FUSE) else ""))
    try:
        with open(PIDFILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    fails = 0
    fast_exits = 0
    sentinel = False

    while True:
        if sentinel:
            time.sleep(SENTINEL_DELAY)
        check_hang()

        t0 = time.time()
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            proc = subprocess.Popen(
                [py, GUARD],
                cwd=HERE,
                creationflags=0x00000008 | 0x08000000,   # DETACHED | NO_WINDOW
                startupinfo=si,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _log("guard.py 已启动 (pid=%d)" % proc.pid)
            rc = proc.wait()
            lived = time.time() - t0
            _log("guard.py 退出 rc=%s 存活 %.0fs" % (rc, lived))
        except Exception as e:
            lived = time.time() - t0
            _log("启动 guard.py 失败: %r" % (e,))

        if lived < FAST_EXIT_THRESHOLD:
            fast_exits += 1
            if fast_exits >= MAX_FAST_EXITS:
                # v1 在这里直接 return（从此没人守护）—— 改为哨兵模式
                _log("guard 连续 %d 次秒退，转入哨兵模式（每 %.0fs 探测一次）"
                     % (fast_exits, SENTINEL_DELAY))
                sentinel = True
                fast_exits = 0
                fails = 0
                continue
        else:
            fast_exits = 0
            sentinel = False

        if lived >= HEALTHY_RESET:
            fails = 0
        fails += 1

        delay = min(RESTART_DELAY * (1 + (fails - 1) * 0.5), 30.0)
        if fails > 1:
            _log("第 %d 次重启，等待 %.1fs" % (fails, delay))
        time.sleep(delay)


if __name__ == "__main__":
    sys.exit(main() or 0)
