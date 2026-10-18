# -*- coding: utf-8 -*-
"""
Edifier Guard 看门狗 v2（watchdog）

职责：保证 guard.py 一直在跑，并且**真的在干活**。

v1 的看门狗只判断"进程有没有退出"。这次事故暴露了两个盲区：
  1. 进程活着但卡死（等 COM、卡在 HID 读、被挂起）—— v1 永远不会重启它
  2. 高权限实例和普通实例同时存在，互斥体互相拦，看门狗疯狂重启
  3. 看门狗自己在 guard 秒退 3 次后直接退出 —— 之后没人管了

v2 的做法：
  - 心跳（共享内存里的一块状态，guard 每 2 秒更新一次）超过 STALE_SECS 没动 -> 判定卡死，杀掉重启
  - 秒退连续 N 次 -> 不再退出，转为低频"哨兵模式"（每 60s 探一次），
    这样 guard 以后恢复正常了还能被拉起来
  - 启动前先做一次 --restore？不做。guard 自己启动时会自愈，
    在这里做只会让设备闪一下又消失
  - 熔断标记存在时仍然启动 guard（它自己会进只观察模式）

★ 运行期零文件：心跳 / PID / 熔断全部在内存里（见 ipc.py），日志默认不写。

用法（一般由 EdifierGuard.exe 或 启动.vbs 拉起，无需手动执行）：
    EdifierGuard.exe            <- 打包后的单文件 exe
    pythonw.exe watchdog.py     <- 源码运行
"""
import os
import subprocess
import sys
import time

import ipc                      # 本地：全内存进程间状态（运行期零文件）

FROZEN = bool(getattr(sys, "frozen", False))


def _app_dir():
    """程序（单文件 exe 或 .py）所在目录。"""
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = _app_dir()
GUARD = os.path.join(APP_DIR, "guard.py")

# ★ 运行期零文件：
#   心跳 / PID / 熔断 / 进程状态 全部走共享内存（见 ipc.py），
#   日志默认**完全不写**；只有设了 EDIFIERGUARD_LOG 才落盘（排查用）。
LOG = os.environ.get("EDIFIERGUARD_LOG") or None

_STATE = None


def state():
    """共享状态（全内存）；不可用返回 None。"""
    global _STATE
    if _STATE is None:
        try:
            _STATE = ipc.create()
        except Exception:
            _STATE = False
    return _STATE or None

RESTART_DELAY = 3.0
HEALTHY_RESET = 60.0
FAST_EXIT_THRESHOLD = 2.0
MAX_FAST_EXITS = 3
STALE_SECS = 180.0        # 心跳超过这么久没更新 -> 判定卡死
SENTINEL_DELAY = 60.0     # 连续秒退后转入哨兵模式的探测间隔
WATCH_INTERVAL = 5.0      # 边等 guard 边巡检的间隔
HANG_GRACE = 30.0         # 刚启动多久内允许还没有心跳（guard 2 秒一跳）

_CANDS = [
    os.path.join(APP_DIR, "runtime", "pythonw.exe"),
    os.path.join(os.path.dirname(sys.executable), "pythonw.exe"),
    sys.executable,
]


def _log(msg):
    """★ 默认不写任何文件；设了 EDIFIERGUARD_LOG 才记录（超过 512KB 轮转）。"""
    if not LOG:
        return
    try:
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
    if FROZEN:
        return sys.executable          # 打包成 exe 后：直接重跑自己（带 --guard）
    for p in _CANDS:
        if p and os.path.isfile(p):
            return p
    return sys.executable


def _guard_cmd(py):
    """构造拉起 guard 的命令行。"""
    if FROZEN:
        return [sys.executable, "--guard"]
    return [py, GUARD]


def _is_our_image(img):
    """进程映像是不是「我们自己」（python / py，或打包后的 exe）。"""
    name = os.path.basename(img or "").lower()
    if not name:
        return True                    # 查不到就保守放行
    if FROZEN:
        return name == os.path.basename(sys.executable).lower()
    return "python" in name or name in ("py.exe", "pyw.exe")


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
    st = state()
    if st is None:
        return 0
    try:
        return st.u32(ipc.OFF_GUARD_PID)
    except Exception:
        return 0


def _hb_age():
    """心跳已经停了多久（秒）；还没跳过返回 None。"""
    st = state()
    if st is None:
        return None
    try:
        ms = st.age_ms()
    except Exception:
        return None
    return None if ms is None else ms / 1000.0


def _proc_image(pid):
    """取进程映像路径（小写）；查不到返回空串。
    杀掉前用它确认目标确实是本程序 —— 防止 PID 陈旧、被系统复用后误杀无关进程。"""
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
    if img and not _is_our_image(img):
        # 共享内存里的 PID 已被系统复用给别的进程 —— 绝不能杀
        _log("忽略陈旧 guard pid=%d（现指向 %s，非本程序），不误杀"
             % (pid, img))
        return False
    age = _hb_age()
    if age is None or age > STALE_SECS:
        _log("★ guard(pid=%d) 心跳已停 %s，判定卡死，强制重启"
             % (pid, "未知" if age is None else "%.0f 秒" % age))
        _kill(pid)
        return True
    return False


_MUTEX_NAME = "Global\\EdifierGuard_Watchdog_v2"


def _already_running():
    """看门狗单实例：防止重复双击，或「启动文件夹 + 根目录」两份同时跑。"""
    import ctypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = ctypes.c_void_p
    k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    h = k.CreateMutexW(None, False, _MUTEX_NAME)
    if not h:
        return False
    return ctypes.get_last_error() == 183      # ERROR_ALREADY_EXISTS


def main():
    if _already_running():
        _log("已有一个看门狗在运行，本实例退出")
        return 0

    if not FROZEN and not os.path.isfile(GUARD):
        _log("找不到 guard.py: %s" % GUARD)
        return 1

    st = state()
    if st is not None:
        st.set_u32(ipc.OFF_WD_PID, os.getpid())
        st.set_u32(ipc.OFF_GUARD_PID, 0)

    py = _python()
    _log("看门狗启动 | python=%s | pid=%d" % (py, os.getpid()))

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
                _guard_cmd(py),
                cwd=APP_DIR,
                creationflags=0x00000008 | 0x08000000,   # DETACHED | NO_WINDOW
                startupinfo=si,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _log("guard.py 已启动 (pid=%d)" % proc.pid)
            # ★ 边等边巡检：guard 进程活着但心跳停了（卡死）要能被发现。
            #   原来直接 proc.wait() 会一直阻塞到 guard 退出，卡死就永远检测不到。
            while proc.poll() is None:
                time.sleep(WATCH_INTERVAL)
                if time.time() - t0 >= HANG_GRACE:
                    check_hang()
            rc = proc.returncode
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
