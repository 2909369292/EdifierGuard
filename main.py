# -*- coding: utf-8 -*-
"""EdifierGuard.exe 的入口（也是 `python main.py` 的入口）。

角色分派
--------
    EdifierGuard.exe                        后台常驻（看门狗 -> 拉起并守护 guard）
    EdifierGuard.exe --guard                守护本体（由看门狗拉起；也可前台单独跑）
    EdifierGuard.exe --doctor / --status …  一次性命令，直接跑 guard 的 CLI
    EdifierGuard.exe --install              安装开机自启（把本程序放进「启动」文件夹）
    EdifierGuard.exe --uninstall            卸载（移除自启 + 停止 + 恢复端点）
    EdifierGuard.exe --version / --help

说明
----
* 打包是无控制台窗口的（--noconsole），所以**双击 / 开机自启时完全无感**。
* 从命令行运行时，会把输出接回父控制台（见 `_attach_console`）。
* 一次性命令的输出**同时**写一份到
  ``%LOCALAPPDATA%\\EdifierGuard\\last-output.txt``，方便事后查看。
* 运行数据（日志 / 配置）固定放在 ``%LOCALAPPDATA%\\EdifierGuard\\``；
  可用环境变量 ``EDIFIERGUARD_HOME`` 改到别处。
"""
import os
import sys

VERSION = "2.1"

# 会“跑完就退出”的一次性命令
SHORT_CLI = ("--doctor", "--status", "--list", "--once", "--restore", "--reset-fuse")
# 全部 CLI 命令（含前台常驻的 --monitor / --debug）
CLI_FLAGS = SHORT_CLI + ("--monitor", "--debug", "--enforce")

HELP = """EdifierGuard — HECATE G3 Max（底座版）音频端点守护

用法:
  EdifierGuard.exe                 后台常驻（自动拉起并守护 guard）
  EdifierGuard.exe --doctor        体检（推荐先跑）
  EdifierGuard.exe --status        当前状态
  EdifierGuard.exe --list          列出 HECATE 音频端点
  EdifierGuard.exe --once          只判定一次
  EdifierGuard.exe --monitor       只观察、不改设备（前台）
  EdifierGuard.exe --debug         前台详细日志
  EdifierGuard.exe --restore       把所有端点恢复为「可用」
  EdifierGuard.exe --reset-fuse    解除熔断
  EdifierGuard.exe --stop          停止后台进程（并把端点恢复为可用）
  EdifierGuard.exe --install       安装开机自启（放进「启动」文件夹）
  EdifierGuard.exe --uninstall     卸载（停止 + 移除自启 + 恢复端点）
  EdifierGuard.exe --version       版本
  EdifierGuard.exe --help          本帮助

说明:
  单文件 exe，**不依赖任何外部路径**（不需要装 Python、不需要 lib/dll）。
  双击 = 静默后台常驻；命令行运行 = 打印结果（也会存一份到 last-output.txt）。
  运行数据目录: %LOCALAPPDATA%\\EdifierGuard\\
"""


# ---------------- 数据目录 ----------------
def _data_dir():
    """与 guard.py / watchdog.py 的 _data_dir() 保持一致。"""
    env = os.environ.get("EDIFIERGUARD_HOME")
    if env:
        return os.path.abspath(env)
    if getattr(sys, "frozen", False):
        la = os.environ.get("LOCALAPPDATA")
        if la and os.path.isdir(la):
            return os.path.join(la, "EdifierGuard")
    return os.path.dirname(os.path.abspath(__file__))


# ---------------- 控制台接管 ----------------
def _ancestor_pids(depth=8):
    """沿进程树向上列出祖先 PID。

    onefile 打包后，本进程的**直接父进程**是 PyInstaller 的引导进程
    （它没有控制台），所以 AttachConsole(-1) 会失败；必须往上找。
    """
    import ctypes
    from ctypes import wintypes as wt

    TH32CS_SNAPPROCESS = 0x2

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD),
                    ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wt.DWORD),
                    ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_char * 260)]

    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return []
    parents = {}
    try:
        e = PROCESSENTRY32()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if k.Process32First(snap, ctypes.byref(e)):
            while True:
                parents[int(e.th32ProcessID)] = int(e.th32ParentProcessID)
                if not k.Process32Next(snap, ctypes.byref(e)):
                    break
    except Exception:
        pass
    finally:
        try:
            k.CloseHandle(ctypes.c_void_p(snap))
        except Exception:
            pass

    out = []
    pid = os.getpid()
    for _ in range(depth):
        pid = parents.get(pid, 0)
        if not pid:
            break
        out.append(pid)
    return out


def _reopen_console():
    import ctypes
    try:
        k = ctypes.windll.kernel32
        k.SetConsoleOutputCP(65001)
        k.SetConsoleCP(65001)
        sys.stdout = open("CONOUT$", "w", encoding="utf-8",
                          buffering=1, errors="replace")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8",
                          buffering=1, errors="replace")
        return True
    except Exception:
        return False


def _attach_console():
    """把 stdout/stderr 接回调用方所在的控制台（没有就返回 False）。"""
    if not getattr(sys, "frozen", False):
        return True
    import ctypes
    k = ctypes.windll.kernel32
    if k.AttachConsole(-1):                 # 直接父进程的控制台
        return _reopen_console()
    for pid in _ancestor_pids(8):           # 打包后：沿进程树往上找
        if k.AttachConsole(pid):
            return _reopen_console()
    return False


class _Tee(object):
    """同时写多个流（控制台 + 文件）。"""

    def __init__(self, *streams):
        self.streams = [s for s in streams if s is not None]

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
            except Exception:
                pass
        return len(data)

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass

    def reconfigure(self, **kw):
        for s in self.streams:
            try:
                s.reconfigure(**kw)
            except Exception:
                pass

    def isatty(self):
        for s in self.streams:
            try:
                if s.isatty():
                    return True
            except Exception:
                pass
        return False


def _stdout_usable():
    """调用方是否给了可用的 stdout（管道 / 重定向文件）。"""
    s = sys.stdout
    if s is None:
        return False
    try:
        return not s.closed
    except Exception:
        return False


def _setup_output(tee=False):
    """准备输出：优先用调用方给的重定向 / 管道，其次接管父控制台。

    tee=True 时再同时写一份 last-output.txt（一次性命令用）。
    """
    if not _stdout_usable():
        _attach_console()
    if not tee:
        return None
    path = os.path.join(_data_dir(), "last-output.txt")
    try:
        f = open(path, "w", encoding="utf-8", errors="replace")
    except Exception:
        return None
    sys.stdout = _Tee(sys.stdout, f)
    sys.stderr = _Tee(sys.stderr, f)
    return path


# ---------------- 自启 / 卸载 ----------------
def _startup_dir():
    """取「启动」文件夹（SHGetKnownFolderPath，比环境变量可靠）。"""
    import ctypes
    import ctypes.wintypes as wt
    try:
        ole32 = ctypes.WinDLL("ole32")
        shell32 = ctypes.WinDLL("shell32")

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD),
                        ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

        # FOLDERID_Startup = {B97D20BB-F46A-4C97-BA10-5E3608430854}
        f = (0xB97D20BB, 0xF46A, 0x4C97,
             (0xBA, 0x10, 0x5E, 0x36, 0x08, 0x43, 0x08, 0x54))
        g = GUID(f[0], f[1], f[2], (ctypes.c_ubyte * 8)(*f[3]))
        ptr = ctypes.c_wchar_p()
        if (shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None,
                                         ctypes.byref(ptr)) == 0 and ptr.value):
            p = ptr.value
            ole32.CoTaskMemFree(ptr)
            return p
    except Exception:
        pass
    a = os.environ.get("APPDATA")
    if a:
        p = os.path.join(a, r"Microsoft\Windows\Start Menu\Programs\Startup")
        if os.path.isdir(p):
            return p
    return None


def _spawn_detached(exe):
    import subprocess
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    subprocess.Popen([exe], cwd=os.path.dirname(exe),
                     creationflags=0x00000008 | 0x08000000,   # DETACHED | NO_WINDOW
                     startupinfo=si,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _kill_ours(data_dir):
    """终止 data_dir 里 pid 文件指向的、且确属本程序的进程。"""
    import ctypes
    import ctypes.wintypes as wt
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wt.HANDLE
    me = os.path.basename(sys.executable).lower()
    killed = []
    for name in ("watchdog.pid", "guard.pid"):
        pf = os.path.join(data_dir, name)
        try:
            with open(pf, "r", encoding="utf-8") as f:
                pid = int(f.read().strip())
        except Exception:
            continue
        if not pid or pid == os.getpid():
            continue
        h = k.OpenProcess(0x1000, False, pid)          # QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            code = ctypes.c_ulong()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:
                continue
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            img = ""
            if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                img = os.path.basename(buf.value).lower()
            if img and img != me and "python" not in img:
                continue                                # PID 被复用，别误杀
        finally:
            k.CloseHandle(h)
        h2 = k.OpenProcess(0x0001, False, pid)          # PROCESS_TERMINATE
        if h2:
            k.TerminateProcess(h2, 0)
            k.CloseHandle(h2)
            killed.append(pid)
        try:
            os.remove(pf)
        except Exception:
            pass
    return killed


def do_install():
    if not getattr(sys, "frozen", False):
        print("--install 仅支持打包后的 EdifierGuard.exe。")
        return 2
    sd = _startup_dir()
    if not sd or not os.path.isdir(sd):
        print("找不到「启动」文件夹。")
        return 2
    src = os.path.abspath(sys.executable)
    dst = os.path.join(sd, os.path.basename(src))
    if os.path.abspath(dst) != src:
        import shutil
        shutil.copy2(src, dst)
        print("已复制到「启动」文件夹：", dst)
    else:
        print("已在「启动」文件夹：", dst)
    old = os.path.join(sd, "EdifierGuard.vbs")      # 旧版方案残留
    if os.path.isfile(old):
        try:
            os.remove(old)
            print("已移除旧版启动器：", old)
        except Exception:
            pass
    _spawn_detached(dst)
    print("已启动。以后每次登录都会自动运行（静默、无窗口、无 UAC）。")
    print("卸载：EdifierGuard.exe --uninstall")
    return 0


def do_stop():
    data = _data_dir()
    killed = _kill_ours(data)
    print("已停止进程:", killed or "（无）")
    try:
        import guard
        ok, detail = guard.restore_all()
        print("恢复音频端点:", "成功" if ok else "失败（%s）" % detail)
    except Exception as e:
        print("恢复音频端点失败:", e)
    return 0


def do_uninstall():
    data = _data_dir()
    do_stop()

    removed = []
    sd = _startup_dir()
    me = os.path.basename(sys.executable).lower()
    if sd and os.path.isdir(sd):
        for n in list(os.listdir(sd)):
            p = os.path.join(sd, n)
            if not os.path.isfile(p):
                continue
            if n.lower() == me or n.lower() == "edifierguard.vbs":
                try:
                    os.remove(p)
                    removed.append(p)
                except Exception as e:
                    print("删除失败:", p, e)
    print("已移除自启:", removed or "（无）")

    print("\n完成。运行数据目录（日志/配置）保留在:", data)
    print("如需一并删除，手动删掉该目录即可。")
    return 0


# ---------------- 主入口 ----------------
def main():
    argv = sys.argv[1:]

    if "--version" in argv:
        _setup_output(tee=False)
        print("EdifierGuard %s" % VERSION)
        return 0
    if "--help" in argv or "-h" in argv:
        _setup_output(tee=False)
        print(HELP)
        return 0
    if "--stop" in argv:
        _setup_output(tee=True)
        return do_stop()
    if "--install" in argv:
        _setup_output(tee=True)
        return do_install()
    if "--uninstall" in argv or "--remove" in argv:
        _setup_output(tee=True)
        return do_uninstall()

    if "--guard" in argv:
        # 由看门狗拉起的子进程：不接控制台，安静地干活
        sys.argv = [sys.argv[0]] + [a for a in argv if a != "--guard"]
        import guard
        return guard.main()

    if any(a in CLI_FLAGS for a in argv):
        _setup_output(tee=any(a in SHORT_CLI for a in argv))
        import guard
        return guard.main()

    # 无参数：后台常驻（静默）
    import watchdog
    return watchdog.main()


if __name__ == "__main__":
    sys.exit(main() or 0)
