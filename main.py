# -*- coding: utf-8 -*-
"""EdifierGuard.exe 的入口（也是 ``python main.py`` 的入口）。

角色分派
--------
    EdifierGuard.exe                        后台常驻（看门狗 -> 拉起并守护 guard）
    EdifierGuard.exe --guard                守护本体（由看门狗拉起）
    EdifierGuard.exe --doctor / --status …  一次性命令
    EdifierGuard.exe --install              把本 exe 复制进「启动」文件夹并启动
    EdifierGuard.exe --uninstall            停止 + 移除自启 + 恢复端点
    EdifierGuard.exe --stop                 只停止（不动自启）
    EdifierGuard.exe --version / --help

★ 运行期「零文件」
------------------
程序在任何情况下都**不会创建**日志 / 配置 / pid / 心跳 / 状态等文件：
进程间状态全在内存里（``ipc.py`` 的共享内存 + 具名互斥体）。

所以就是这么简单：
  把这**一个** ``EdifierGuard.exe`` 复制到任何一台 Windows 电脑，
  丢进「启动」文件夹，开机就会静默运行，不留任何痕迹、不需要任何配置。

唯一的例外（默认关闭）：设了环境变量 ``EDIFIERGUARD_LOG=<路径>`` 才写日志。
"""
import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
import sys
import time

import ipc

VERSION = "2.1"

# 一次性命令（跑完就退出）
SHORT_CLI = ("--doctor", "--status", "--list", "--once", "--restore", "--reset-fuse")
# 前台常驻命令（会一直跑，输出直接进控制台）
FOREGROUND = ("--monitor", "--debug", "--enforce")
CLI_FLAGS = SHORT_CLI + FOREGROUND

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
  * 单文件 exe，**不依赖任何外部文件或路径**：不需要装 Python，
    也不需要 lib/dll，**运行时不产生任何日志 / 配置 / pid 文件**。
  * 复制到任何一台 Windows 电脑都能直接用；
    放进「启动」文件夹即可开机自启。
  * 想排查问题时，设个环境变量即可开日志：
        set EDIFIERGUARD_LOG=%TEMP%\\edifierguard.log
"""


# ================= 输出（控制台 / 弹窗） =================
def _stdout_usable():
    """调用方是否给了可用的 stdout（管道 / 重定向文件 / 控制台）。"""
    s = sys.stdout
    if s is None:
        # --noconsole 下 PyInstaller 把 sys.stdout 设成 None，
        # 但父进程给的管道 / 重定向文件其实还在，接回来即可。
        return _adopt_std_handle()
    try:
        return not s.closed
    except Exception:
        return False


def _adopt_std_handle():
    """把父进程留下的 stdout 句柄接回 sys.stdout。

    这样 ``EdifierGuard.exe --status > o.txt``、
    ``for /f %%i in ('EdifierGuard.exe --status')`` 才能正常工作
    （也顺带避免输出与控制台交错、编码错乱）。
    只接管管道 / 文件句柄；控制台句柄交给 _attach_console 处理。
    """
    try:
        import msvcrt
        k = ctypes.windll.kernel32
        k.GetStdHandle.restype = ctypes.c_void_p        # ★ 64 位句柄，别被截断
        k.GetStdHandle.argtypes = [ctypes.c_int]
        k.GetFileType.restype = ctypes.c_uint
        k.GetFileType.argtypes = [ctypes.c_void_p]
        h = k.GetStdHandle(-11)                         # STD_OUTPUT_HANDLE
        if not h or h == ctypes.c_void_p(-1).value:
            return False
        if k.GetFileType(ctypes.c_void_p(h)) in (0, 2):  # UNKNOWN / CHAR(控制台)
            return False
        fd = msvcrt.open_osfhandle(h, os.O_WRONLY)
        sys.stdout = os.fdopen(fd, "w", encoding="utf-8",
                               errors="replace", buffering=1)
        return True
    except Exception:
        return False


def _reopen_console():
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
    """把 stdout/stderr 接回调用方所在的控制台。

    打包成 onefile 后本进程的**直接父进程**是 PyInstaller 的引导进程
    （没有控制台），所以 AttachConsole(-1) 会失败；这里沿进程树往上找。
    """
    if not getattr(sys, "frozen", False):
        return True
    k = ctypes.windll.kernel32
    if k.AttachConsole(-1):
        return _reopen_console()
    for pid in _ancestor_pids(8):
        if k.AttachConsole(pid):
            return _reopen_console()
    return False


def _message_box(text, title="EdifierGuard"):
    """双击运行、又没有控制台时，用弹窗展示结果（不落任何文件）。"""
    try:
        ctypes.windll.user32.MessageBoxW(None, (text or "(无输出)")[:4000],
                                         title, 0x40)
    except Exception:
        pass


def _run(fn):
    """跑一个「一次性命令」：优先控制台，其次弹窗。"""
    if _stdout_usable() or _attach_console():
        return fn()
    import contextlib
    import io
    buf = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = fn() or 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 0
    except Exception as e:
        buf.write("\n[错误] %r" % (e,))
    _message_box(buf.getvalue().strip())
    return code


# ================= 进程工具 =================
def _snapshot():
    """返回 {pid: (父 pid, 映像名小写)}。"""
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
    snap = k.CreateToolhelp32Snapshot(0x2, 0)          # TH32CS_SNAPPROCESS
    out = {}
    if not snap or snap == ctypes.c_void_p(-1).value:
        return out
    try:
        e = PROCESSENTRY32()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if k.Process32First(snap, ctypes.byref(e)):
            while True:
                out[int(e.th32ProcessID)] = (
                    int(e.th32ParentProcessID),
                    e.szExeFile.decode("mbcs", "replace").lower())
                if not k.Process32Next(snap, ctypes.byref(e)):
                    break
    except Exception:
        pass
    finally:
        try:
            k.CloseHandle(ctypes.c_void_p(snap))
        except Exception:
            pass
    return out


def _ancestor_pids(depth=8):
    snap = _snapshot()
    out = []
    pid = os.getpid()
    for _ in range(depth):
        pid = snap.get(pid, (0, ""))[0]
        if not pid:
            break
        out.append(pid)
    return out


def _terminate(pid):
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wt.HANDLE
    k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    h = k.OpenProcess(0x0001, False, pid)              # PROCESS_TERMINATE
    if not h:
        return False
    try:
        return bool(k.TerminateProcess(h, 0))
    finally:
        k.CloseHandle(h)


def _kill_running():
    """结束共享状态里记录的进程（先看门狗、后 guard，避免立刻被拉起来）。"""
    wd, guard = ipc.running_pids()
    me = os.path.basename(sys.executable).lower()
    killed = []
    for pid in (wd, guard):
        if not pid or pid == os.getpid():
            continue
        # 确认目标确实是我们自己（防止 PID 被复用后误杀无关进程）
        img = ""
        try:
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.OpenProcess.restype = wt.HANDLE
            h = k.OpenProcess(0x1000, False, pid)     # QUERY_LIMITED_INFORMATION
            if h:
                buf = ctypes.create_unicode_buffer(1024)
                size = ctypes.c_ulong(1024)
                if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    img = os.path.basename(buf.value).lower()
                k.CloseHandle(h)
        except Exception:
            pass
        if img and img != me and "python" not in img:
            continue
        if _terminate(pid):
            killed.append(pid)
    return killed


def _sweep_ours():
    """兜底：结束所有残留的 EdifierGuard 进程（排除自己和自己的父进程）。

    只在打包后执行 —— 源码运行时映像名是 python.exe，按名字扫会误伤。
    """
    if not getattr(sys, "frozen", False):
        return []
    snap = _snapshot()
    me = os.path.basename(sys.executable).lower()
    skip = {os.getpid()} | set(_ancestor_pids(4))
    killed = []
    for pid, (_ppid, name) in snap.items():
        if pid in skip or name != me:
            continue
        if _terminate(pid):
            killed.append(pid)
    return killed


def _others_count():
    """当前还有几个别的 EdifierGuard 进程在跑。"""
    if not getattr(sys, "frozen", False):
        return 0
    me = os.path.basename(sys.executable).lower()
    skip = {os.getpid()} | set(_ancestor_pids(4))
    return len([p for p, (_pp, n) in _snapshot().items()
                if p not in skip and n == me])


def _wait_gone(timeout=15.0):
    """等所有别的 EdifierGuard 进程真正退出。

    ★ TerminateProcess 返回后进程还没完全消失，exe 文件仍然被占用，
      紧接着 copy2 会报 WinError 32（另一个程序正在使用此文件）。
    """
    end = time.time() + timeout
    while time.time() < end:
        if _others_count() == 0:
            time.sleep(0.3)                 # 再给文件锁一点释放时间
            return True
        time.sleep(0.3)
    return False


# ================= 自启 / 停止 / 卸载 =================
def _startup_dir():
    """取「启动」文件夹（SHGetKnownFolderPath，比环境变量可靠）。"""
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
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    subprocess.Popen([exe], cwd=os.path.dirname(exe),
                     creationflags=0x00000008 | 0x08000000,   # DETACHED | NO_WINDOW
                     startupinfo=si,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _cleanup_legacy():
    """★ 取消历史上出现过的其它自启 / 安装方式（用户明确要求）。

    返回 (removed, needs_admin)：后者需要管理员权限才能删（会提示用户）。
    """
    removed = []
    needs_admin = []

    # 1) 「启动」文件夹里的旧启动器（旧方案是放一个 .vbs）
    #    以及卸载时因为「正在运行删不掉」而改名留下的 *.removed
    sd = _startup_dir()
    if sd and os.path.isdir(sd):
        for n in ("EdifierGuard.vbs", "EdifierGuard.bat", "EdifierGuard.cmd",
                  "EdifierGuard.lnk", "Edifier Guard.vbs",
                  "EdifierGuard.exe.removed", "EdifierGuard.exe.old"):
            p = os.path.join(sd, n)
            if os.path.isfile(p):
                try:
                    os.remove(p)
                    removed.append(p)
                except Exception:
                    pass

    # 2) 注册表 Run 项
    try:
        import winreg
        for hive, tag in ((winreg.HKEY_CURRENT_USER, "HKCU"),
                          (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
            path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            try:
                key = winreg.OpenKey(hive, path, 0, winreg.KEY_ALL_ACCESS)
            except Exception:
                continue
            try:
                hit = []
                i = 0
                while True:
                    try:
                        nm, val, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    i += 1
                    if "edifierguard" in (nm or "").lower() or \
                       "edifierguard" in str(val or "").lower():
                        hit.append(nm)
                for nm in hit:
                    try:
                        winreg.DeleteValue(key, nm)
                        removed.append("%s Run: %s" % (tag, nm))
                    except Exception:
                        pass
            finally:
                winreg.CloseKey(key)
    except Exception:
        pass

    # 3) 旧计划任务（多半是当年用管理员建的，普通权限删不掉 -> 提示用户）
    for tn in ("EdifierGuard", "Edifier Guard", "EdifierGuardWatch"):
        try:
            q = subprocess.run(["schtasks", "/Query", "/TN", tn],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            if q.returncode != 0:
                continue                        # 本来就没有
            r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", tn],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                removed.append("计划任务: %s" % tn)
            else:
                needs_admin.append("计划任务: %s" % tn)
        except Exception:
            pass

    # 4) 上一版方案的固定目录 %LOCALAPPDATA%\EdifierGuard
    la = os.environ.get("LOCALAPPDATA")
    if la:
        legacy = os.path.join(la, "EdifierGuard")
        if os.path.isdir(legacy):
            shutil.rmtree(legacy, ignore_errors=True)
            if not os.path.isdir(legacy):
                removed.append(legacy)

    return removed, needs_admin


def do_stop():
    killed = _kill_running()
    extra = _sweep_ours()
    for p in extra:
        if p not in killed:
            killed.append(p)
    print("已停止进程:", killed or "（无）")
    _wait_gone()
    try:
        import guard
        ok, detail = guard.restore_all()
        print("恢复音频端点:", "成功" if ok else "失败（%s）" % detail)
    except Exception as e:
        print("恢复音频端点失败:", e)
    return 0


def do_install():
    if not getattr(sys, "frozen", False):
        print("--install 只支持打包后的 EdifierGuard.exe。")
        return 2

    sd = _startup_dir()
    if not sd or not os.path.isdir(sd):
        print("找不到「启动」文件夹。")
        return 2

    src = os.path.abspath(sys.executable)
    dst = os.path.join(sd, os.path.basename(src))

    removed, needs_admin = _cleanup_legacy()
    if removed:
        print("已清理旧方案:")
        for x in removed:
            print("   -", x)
    if needs_admin:
        print("以下旧方案需要管理员权限才能删除:")
        for x in needs_admin:
            print("   !", x)
        print("   -> 请双击运行 scripts\\CLEAN_OLD_TASK.bat（会请求一次 UAC）")

    killed = _kill_running()
    extra = _sweep_ours()
    for p in extra:
        if p not in killed:
            killed.append(p)
    if killed:
        print("已停止旧的进程:", killed)
    if not _wait_gone():
        print("警告：仍有旧进程没退出，稍后可能复制失败。")

    if os.path.abspath(dst) != src:
        ok = False
        for _ in range(12):
            try:
                shutil.copy2(src, dst)
                ok = True
                break
            except PermissionError:
                time.sleep(0.5)
        if not ok:
            print("复制失败：%s 正在被占用，请稍后重试。" % dst)
            return 1
        print("已复制到「启动」文件夹:", dst)
    else:
        print("已在「启动」文件夹:", dst)

    _spawn_detached(dst)
    print("已启动。以后每次登录都会静默运行（无窗口、无 UAC、不留任何文件）。")
    print("卸载: EdifierGuard.exe --uninstall")
    return 0


def do_uninstall():
    do_stop()
    _wait_gone()

    sd = _startup_dir()
    me = os.path.basename(sys.executable).lower()
    legacy = (me, "edifierguard.vbs", "edifierguard.bat", "edifierguard.cmd")
    removed = []
    failed = []

    if sd and os.path.isdir(sd):
        for n in list(os.listdir(sd)):
            if n.lower() not in legacy:
                continue
            p = os.path.join(sd, n)
            if not os.path.isfile(p):
                continue
            for _ in range(10):                 # 刚被杀掉的进程可能还占着文件
                try:
                    os.remove(p)
                    removed.append(p)
                    break
                except Exception:
                    time.sleep(0.5)
            else:
                # ★ 正在运行的 exe 删不掉（WinError 5），但可以改名 ——
                #   改掉后缀就等于「不再自启」，退出后随时能手动删。
                alt = p + ".removed"
                try:
                    os.replace(p, alt)
                    removed.append(alt + "  （正被占用，已改名；退出后可手动删除）")
                except Exception as e:
                    failed.append("%s  (%s)" % (p, e))

    if removed:
        for x in removed:
            print("已移除自启:", x)
    elif not failed:
        print("「启动」文件夹里没有本程序的自启项（本来就没装）。")
    for x in failed:
        print("移除失败:", x)
        print("   -> 请手动删除「启动」文件夹里的 EdifierGuard.exe")

    print("\n完成：进程已停止、音频端点已恢复、自启已移除。")
    print("本程序运行期不产生任何文件；删掉 EdifierGuard.exe 这一个文件就彻底干净了。")
    return 0


# ================= 主入口 =================
def main():
    argv = sys.argv[1:]

    if "--version" in argv:
        _run(lambda: print("EdifierGuard %s" % VERSION) or 0)
        return 0
    if "--help" in argv or "-h" in argv:
        _run(lambda: print(HELP) or 0)
        return 0
    if "--install" in argv:
        return _run(do_install)
    if "--uninstall" in argv or "--remove" in argv:
        return _run(do_uninstall)
    if "--stop" in argv:
        return _run(do_stop)

    if "--guard" in argv:
        # 由看门狗拉起的子进程：不接控制台，安静地干活
        sys.argv = [sys.argv[0]] + [a for a in argv if a != "--guard"]
        import guard
        return guard.main()

    if any(a in FOREGROUND for a in argv):
        if not _stdout_usable():
            _attach_console()
        import guard
        return guard.main()

    if any(a in CLI_FLAGS for a in argv):
        def _cli():
            import guard
            return guard.main()
        return _run(_cli)

    # 无参数：后台常驻（静默）
    import watchdog
    return watchdog.main()


if __name__ == "__main__":
    sys.exit(main() or 0)
