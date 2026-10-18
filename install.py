# -*- coding: utf-8 -*-
"""
Edifier Guard 安装器（启动文件夹方案 · 免管理员）

职责：
  1. 找到可用的 Python 解释器
  2. 在「启动」文件夹里放一个 VBS 启动器（登录时静默自启，无黑窗）
  3. 立即启动守护进程
  4. 验证并打印状态

为什么不需要管理员：
  守护进程用的是 IPolicyConfig::SetEndpointVisibility，
  这是 Windows 设置应用内部用的同一个接口 —— 普通用户权限即可切换音频设备。
  所以安装和运行全流程都不需要 UAC。

同时会尝试清理旧的计划任务（如果有，且当前有权限）。
"""
import ctypes
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "guard.py")
WATCHDOG = os.path.join(HERE, "watchdog.py")
SELFTEST = os.path.join(HERE, "selftest.py")
LOG = os.path.join(HERE, "guard.log")
TASK_NAME = "EdifierGuard"
LAUNCHER = "EdifierGuard.vbs"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def _py_candidates():
    """按优先级列出可能的解释器路径（不写死任何用户目录）。

    顺序：
      1. 项目自带的 runtime\\python[w].exe（便携版）
      2. 当前解释器所在目录的 pythonw.exe
      3. 当前解释器自身
      4. PATH 里的 pythonw.exe / python.exe
    """
    cands = [
        os.path.join(HERE, "runtime", "pythonw.exe"),
        os.path.join(HERE, "runtime", "python.exe"),
    ]
    exedir = os.path.dirname(sys.executable or "")
    if exedir:
        cands.append(os.path.join(exedir, "pythonw.exe"))
    if sys.executable:
        cands.append(sys.executable)
    for name in ("pythonw.exe", "python.exe"):
        w = shutil.which(name)
        if w:
            cands.append(w)
    return cands


def _from_py_launcher():
    """PATH 里没有 python.exe 时，退回 Windows 的 py 启动器解析真实路径。"""
    for launcher in ("py.exe", "py"):
        exe = shutil.which(launcher)
        if not exe:
            continue
        for tag in ("-3", ""):
            try:
                args = [exe] + ([tag] if tag else []) + \
                       ["-c", "import sys;print(sys.executable)"]
                r = subprocess.run(args, capture_output=True, text=True,
                                   timeout=10)
                lines = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
                real = lines[-1] if lines else ""
                if real and os.path.isfile(real):
                    w = os.path.join(os.path.dirname(real), "pythonw.exe")
                    return w if os.path.isfile(w) else real
            except Exception:
                pass
    return None


def _looks_like_python(p):
    """排除 Microsoft Store 的 python.exe 占位别名（它不是真正的解释器）。"""
    return bool(p) and os.path.isfile(p) and "windowsapps" not in p.lower()


def find_python():
    """优先 pythonw.exe（无控制台窗口）"""
    for p in _py_candidates():
        if _looks_like_python(p):
            return p
    return _from_py_launcher()


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _shell_folder(csidl):
    """用 SHGetKnownFolderPath 取系统已知文件夹（比环境变量可靠）"""
    try:
        import ctypes.wintypes as wt
        ole32 = ctypes.WinDLL("ole32")
        shell32 = ctypes.WinDLL("shell32")

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD),
                        ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

        # CSIDL -> KNOWNFOLDERID 映射（只实现用到的两个）
        KNOWN = {
            0x0007: (0xB97D20BB, 0xF46A, 0x4C97, (0xBA, 0x10, 0x5E, 0x36, 0x08, 0x43, 0x08, 0x54)),
            0x001A: (0x3EB685DB, 0x65F9, 0x4CF6, (0xA3, 0xAE, 0x3A, 0xF7, 0xEF, 0x3D, 0x48, 0x4D)),
        }
        d = KNOWN.get(csidl)
        if not d:
            return None
        kg = GUID(d[0], d[1], d[2], (ctypes.c_ubyte * 8)(*d[3]) if len(d[3]) == 8 else d[3])
        ptr = ctypes.c_wchar_p()
        hr = shell32.SHGetKnownFolderPath(ctypes.byref(kg), 0, None,
                                         ctypes.byref(ptr))
        if hr != 0 or not ptr.value:
            return None
        path = ptr.value
        ole32.CoTaskMemFree(ptr)
        return path
    except Exception:
        return None


def startup_dir():
    # 1) 已知文件夹 API（最可靠）
    p = _shell_folder(0x0007)      # CSIDL_STARTUP
    if p and os.path.isdir(p):
        return p
    # 2) APPDATA 环境变量
    a = os.environ.get("APPDATA")
    if a:
        p = os.path.join(a, r"Microsoft\Windows\Start Menu\Programs\Startup")
        if os.path.isdir(p):
            return p
    # 3) 从 USERPROFILE 拼
    u = os.environ.get("USERPROFILE")
    if u:
        p = os.path.join(u, r"AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup")
        if os.path.isdir(p):
            return p
    # 4) 用 PowerShell 问
    rc, out, _ = run_ps("[Environment]::GetFolderPath('Startup')")
    out = (out or "").strip()
    if rc == 0 and out and os.path.isdir(out):
        return out
    return None


def run_ps(script, timeout=60):
    """把 PowerShell 脚本写入临时文件后执行，避免引号问题"""
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".ps1", dir=HERE)
    os.close(fd)
    try:
        # 统一用 utf-8 读写；输出强制 utf-8 避免中文乱码
        script = "[Console]::OutputEncoding=[Text.Encoding]::UTF8\r\n" + script
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(script)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=env)
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:
        return -2, "", str(e)
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


def make_launcher(pyw, target, extra=None):
    """
    生成 VBS 启动器。
    VBS 的好处：用 WScript.Shell.Run 的窗口模式 0 启动，
    即使误用 python.exe 也不会闪黑窗。
    extra: 传给脚本的额外参数（可选）
    """
    cmd = '"""%s"" ""%s"""' % (pyw, target)
    if extra:
        cmd += ' ""%s""' % extra
    vbs = (
        'Set sh = CreateObject("WScript.Shell")\r\n'
        'sh.CurrentDirectory = "%s"\r\n'
        'sh.Run %s, 0, False\r\n'
    ) % (HERE, cmd)
    path = os.path.join(startup_dir(), LAUNCHER)
    with open(path, "w", encoding="mbcs", errors="replace") as f:
        f.write(vbs)
    return path


def stop_existing():
    """终止已有的 guard.py / watchdog.py 进程。

    v1 用「镜像路径里是否含 edifier-probe」判断，但 python.exe 的路径里根本没有
    项目名，实际一个都杀不掉（等于空操作）。v2 改为读它们自己写的 PID 文件，
    并校验进程确实还活着、确实是我们启动的 python。
    """
    import ctypes.wintypes as wt
    k = ctypes.WinDLL("kernel32.dll")
    k.OpenProcess.restype = wt.HANDLE
    killed = []

    for name in ("guard.pid", "watchdog.pid"):
        pf = os.path.join(HERE, name)
        try:
            with open(pf, "r", encoding="utf-8") as f:
                pid = int(f.read().strip())
        except Exception:
            continue
        if not pid or pid == os.getpid():
            continue
        h = k.OpenProcess(0x1000, False, pid)     # QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            code = ctypes.c_ulong()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:
                continue                          # 已退出
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            img = ""
            if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                img = buf.value.lower()
            if "python" not in img:
                continue                          # PID 被复用了，不是我们
        finally:
            k.CloseHandle(h)

        h2 = k.OpenProcess(0x0001, False, pid)    # TERMINATE
        if h2:
            k.TerminateProcess(h2, 0)
            k.CloseHandle(h2)
            killed.append(pid)
        try:
            os.remove(pf)
        except Exception:
            pass
    return killed


def main():
    print("=" * 62)
    print("  Edifier Guard - 安装（启动文件夹方案 · 免管理员）")
    print("=" * 62)
    print("  当前管理员权限 :", "是" if is_admin() else "否（无需）")

    py = find_python()
    if not py:
        print("\n[错误] 找不到 Python 解释器，已尝试：")
        for p in _py_candidates():
            print("   ", p)
        print("     （以及 Windows 的 py 启动器）")
        _pause()
        return
    print("  Python         :", py)

    if not os.path.isfile(GUARD):
        print("\n[错误] 找不到 guard.py:", GUARD)
        _pause()
        return
    print("  guard.py       :", GUARD)

    # --- 1. 清理旧的计划任务（尽力而为，失败不影响）---
    print("\n[1/4] 清理旧计划任务（如有）...")
    rc, out, _ = run_ps("""
$ErrorActionPreference = 'SilentlyContinue'
$t = Get-ScheduledTask -TaskName '%s'
if ($t) {
    Unregister-ScheduledTask -TaskName '%s' -Confirm:$false -ErrorAction SilentlyContinue
    if ($?) { Write-Host '  [OK] 旧计划任务已移除' }
    else { Write-Host '  [跳过] 无权限移除旧计划任务（不影响本方案）' }
} else {
    Write-Host '  [跳过] 没有旧计划任务'
}
""" % (TASK_NAME, TASK_NAME))
    print((out or "").rstrip() or "  [跳过]")

    # --- 2. 写入启动文件夹 ---
    print("\n[2/4] 安装开机自启（启动文件夹）...")
    sd = startup_dir()
    if not sd or not os.path.isdir(sd):
        print("  [错误] 找不到启动文件夹:", sd)
        _pause()
        return
    if not os.path.isfile(WATCHDOG):
        print("  [错误] 找不到 watchdog.py:", WATCHDOG)
        _pause()
        return
    try:
        # 启动看门狗（它会负责拉起 guard.py 并在其崩溃后自动重启）
        path = make_launcher(py, WATCHDOG)
        print("  [OK]", path)
    except Exception as e:
        print("  [错误] 写入失败:", e)
        _pause()
        return

    # --- 3. 启动守护进程（看门狗 + guard）---
    print("\n[3/4] 启动守护进程...")
    killed = stop_existing()
    if killed:
        print("  已终止旧进程:", killed)
        time.sleep(0.8)
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        subprocess.Popen([py, WATCHDOG], cwd=HERE,
                         creationflags=0x00000008 | 0x08000000,   # DETACHED | NO_WINDOW
                         startupinfo=si,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3.5)
        print("  已启动（看门狗 + guard）")
    except Exception as e:
        print("  [错误] 启动失败:", e)

    # --- 4. 验证 ---
    print("\n[4/4] 验证...")
    try:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        # 用带控制台的 python.exe 跑验证（pythonw 没有 stdout，capture 会是空）
        pyc = py
        if py.lower().endswith("pythonw.exe"):
            cand = py[:-len("pythonw.exe")] + "python.exe"
            if os.path.isfile(cand):
                pyc = cand
        r = subprocess.run([pyc, GUARD, "--status"], capture_output=True,
                           encoding="utf-8", errors="replace",
                           timeout=30, env=env, cwd=HERE)
        for line in (r.stdout or "").splitlines():
            print("  " + line)
    except Exception as e:
        print("  验证失败:", repr(e))

    print("\n" + "=" * 62)
    print("  安装完成")
    print("=" * 62)
    print("""
  守护进程已在后台运行（guard v2），并且会：
    - 每次登录自动启动（静默、无窗口、无 UAC 弹窗）
    - 看门狗守护：进程退出或心跳停止（卡死）都会自动重启
    - 每次操作后用 Windows 音频 API 独立校验，不达标就回滚成"可用"
    - 连续失败 3 次自动熔断（guard.fused），转只观察，不再碰设备
    - 底层设备被禁用（PnP 问题码 22）时完全不动端点，只写日志

  建议先跑一次体检：
    "%s" "%s" --doctor

  日志文件 : %s
  看门狗日志: %s

  手动管理：
    体检   : "%s" "%s" --doctor
    看状态 : "%s" "%s" --status
    看端点 : "%s" "%s" --list
    自测   : "%s" "%s" --inject   （会临时弄坏再修复，验证自愈）
    前台调试: "%s" "%s" --debug
    只观察 : "%s" "%s" --monitor
    解除熔断: "%s" "%s" --reset-fuse
    卸载    : 删除启动文件夹里的 %s，
              再执行  "%s" "%s" --restore  （恢复设备可用）
""" % (py, GUARD, LOG, os.path.join(HERE, "watchdog.log"),
       py, GUARD, py, GUARD, py, GUARD, py, SELFTEST, py, GUARD,
       py, GUARD, py, GUARD, LAUNCHER, py, GUARD))

    _pause()


def _pause():
    if "--no-pause" in sys.argv:
        return
    try:
        input("\n按回车键退出...")
    except EOFError:
        pass


if __name__ == "__main__":
    main()
