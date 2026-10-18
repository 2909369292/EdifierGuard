# -*- coding: utf-8 -*-
"""
Edifier Guard 安装器（自包含安装 · 免管理员）

做了什么
--------
  1. 找到可用的 Python 解释器
  2. 把运行所需的**全部文件复制**到一个固定目录：
         %LOCALAPPDATA%\\EdifierGuard
     （环境变量 EDIFIERGUARD_HOME 可覆盖；用于开发/测试）
  3. 在「启动」文件夹放一个约 1KB 的 VBS 启动器，指向固定目录里的 watchdog.py
  4. 立即启动看门狗（由它拉起并守护 guard.py）
  5. 验证并打印状态

为什么这样最稳
--------------
  * 固定目录与**项目文件夹完全解耦**：项目随便移动/重命名/删除，
    开机自启都不受影响（启动器指向的是固定目录，不是项目目录）。
  * 启动文件夹里只放一个小的启动器，而不是整包：
      - Windows 不会执行「启动」文件夹子目录里的东西，所以程序本体必须另放；
      - 也不会把一堆 .py 暴露在启动项里、被误执行。
  * 全程免管理员（SetEndpointVisibility 普通用户权限即可）。

卸载
----
    python install.py --uninstall          停止 + 移除启动器（保留固定目录）
    python install.py --uninstall --purge  连同固定目录一起删除
"""
import ctypes
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TASK_NAME = "EdifierGuard"
LAUNCHER = "EdifierGuard.vbs"
APP_DIR_NAME = "EdifierGuard"

# 需要复制到固定目录的内容
COPY_FILES = ["guard.py", "watchdog.py", "install.py", "guard_config.json"]
COPY_TREES = ["lib", "runtime", "tools", "scripts"]
KEEP_IF_EXISTS = ["guard_config.json"]     # 已有配置不覆盖（保留用户设置）

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ===================== 解释器探测 =====================
def _py_candidates():
    """按优先级列出可能的解释器路径（不写死任何用户目录）。"""
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
    for p in _py_candidates():
        if _looks_like_python(p):
            return p
    return _from_py_launcher()


def _python_exe(pyw):
    """从 pythonw.exe 推出同目录的 python.exe（验证时要用带控制台的）。"""
    if pyw and pyw.lower().endswith("pythonw.exe"):
        cand = pyw[:-len("pythonw.exe")] + "python.exe"
        if os.path.isfile(cand):
            return cand
    return pyw


# ===================== 路径 =====================
def local_appdata():
    la = os.environ.get("LOCALAPPDATA")
    if la and os.path.isdir(la):
        return la
    up = os.environ.get("USERPROFILE")
    if up:
        p = os.path.join(up, "AppData", "Local")
        if os.path.isdir(p):
            return p
    return None


def install_dir():
    env = os.environ.get("EDIFIERGUARD_HOME")
    if env:
        return os.path.abspath(env)
    la = local_appdata()
    if la:
        return os.path.join(la, APP_DIR_NAME)
    return HERE          # 兜底：就地安装


def _shell_folder(csidl):
    """用 SHGetKnownFolderPath 取系统已知文件夹（比环境变量可靠）。"""
    try:
        import ctypes.wintypes as wt
        ole32 = ctypes.WinDLL("ole32")
        shell32 = ctypes.WinDLL("shell32")

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD),
                        ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

        KNOWN = {
            0x0007: (0xB97D20BB, 0xF46A, 0x4C97, (0xBA, 0x10, 0x5E, 0x36, 0x08, 0x43, 0x08, 0x54)),
            0x001A: (0x3EB685DB, 0x65F9, 0x4CF6, (0xA3, 0xAE, 0x3A, 0xF7, 0xEF, 0x3D, 0x48, 0x4D)),
        }
        d = KNOWN.get(csidl)
        if not d:
            return None
        kg = GUID(d[0], d[1], d[2], (ctypes.c_ubyte * 8)(*d[3]))
        ptr = ctypes.c_wchar_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(kg), 0, None,
                                        ctypes.byref(ptr)) != 0 or not ptr.value:
            return None
        path = ptr.value
        ole32.CoTaskMemFree(ptr)
        return path
    except Exception:
        return None


def startup_dir():
    p = _shell_folder(0x0007)      # CSIDL_STARTUP
    if p and os.path.isdir(p):
        return p
    a = os.environ.get("APPDATA")
    if a:
        p = os.path.join(a, r"Microsoft\Windows\Start Menu\Programs\Startup")
        if os.path.isdir(p):
            return p
    u = os.environ.get("USERPROFILE")
    if u:
        p = os.path.join(u, r"AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup")
        if os.path.isdir(p):
            return p
    rc, out, _ = run_ps("[Environment]::GetFolderPath('Startup')")
    out = (out or "").strip()
    if rc == 0 and out and os.path.isdir(out):
        return out
    return None


def run_ps(script, timeout=60):
    """把 PowerShell 脚本写入临时文件后执行，避免引号问题。"""
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".ps1", dir=HERE)
    os.close(fd)
    try:
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


# ===================== 复制（打包） =====================
def stage(src, dst):
    """把运行所需文件从 src 复制到固定目录 dst。返回复制清单。"""
    copied = []
    if os.path.abspath(src) == os.path.abspath(dst):
        return copied
    os.makedirs(dst, exist_ok=True)

    for name in COPY_FILES:
        s = os.path.join(src, name)
        if not os.path.isfile(s):
            continue
        d = os.path.join(dst, name)
        if name in KEEP_IF_EXISTS and os.path.isfile(d):
            continue                        # 保留已有配置
        shutil.copy2(s, d)
        copied.append(name)

    for name in COPY_TREES:
        s = os.path.join(src, name)
        if not os.path.isdir(s):
            continue
        for root, dirs, files in os.walk(s):
            dirs[:] = [x for x in dirs if x != "__pycache__"]
            rel = os.path.relpath(root, src)
            tgt = os.path.join(dst, rel)
            os.makedirs(tgt, exist_ok=True)
            for f in files:
                if f.endswith((".pyc", ".pyo")):
                    continue
                shutil.copy2(os.path.join(root, f), os.path.join(tgt, f))
        copied.append(name + "/")
    return copied


# ===================== 启动器 =====================
_VBS_TMPL = '''Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
app = "@APP@"
target = app & "\\watchdog.py"
py = "@PY@"
If Not fso.FileExists(py) Then py = ""
If py = "" Then
    For Each c In Array(app & "\\runtime\\pythonw.exe", app & "\\runtime\\python.exe")
        If fso.FileExists(c) Then
            py = c
            Exit For
        End If
    Next
End If
If py = "" Then
    For Each c In Array("pythonw.exe", "python.exe", "pyw.exe -3", "py.exe -3")
        If sh.Run("cmd /c where " & Split(c, " ")(0) & " >nul 2>nul", 0, True) = 0 Then
            py = c
            Exit For
        End If
    Next
End If
If fso.FileExists(target) And py <> "" Then
    sh.CurrentDirectory = app
    sh.Run """" & py & """ """ & target & """", 0, False
End If
'''


def make_launcher(app_dir, pyw):
    """在「启动」文件夹生成静默启动器（窗口模式 0，无黑窗）。"""
    vbs = _VBS_TMPL.replace("@APP@", app_dir).replace("@PY@", pyw or "")
    # 统一成 CRLF（模板源码本身可能已是 CRLF，避免出现 \r\r\n）
    vbs = vbs.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    path = os.path.join(startup_dir(), LAUNCHER)
    with open(path, "w", encoding="mbcs", errors="replace", newline="") as f:
        f.write(vbs)
    return path


# ===================== 进程控制 =====================
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def stop_dir(d):
    """终止 d 目录下 guard.pid / watchdog.pid 指向的进程（校验确为 python）。"""
    import ctypes.wintypes as wt
    k = ctypes.WinDLL("kernel32.dll")
    k.OpenProcess.restype = wt.HANDLE
    killed = []
    for name in ("watchdog.pid", "guard.pid"):
        pf = os.path.join(d, name)
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
                continue
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            img = ""
            if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                img = buf.value.lower()
            if "python" not in img:
                continue
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


# ===================== 安装 / 卸载 =====================
def _pause():
    if "--no-pause" in sys.argv:
        return
    try:
        input("\n按回车键退出...")
    except EOFError:
        pass


def cleanup_task():
    print("\n[1/5] 清理旧计划任务（如有）...")
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


def do_install():
    app = install_dir()

    print("=" * 62)
    print("  Edifier Guard - 安装（自包含安装 · 免管理员）")
    print("=" * 62)
    print("  管理员权限 :", "是" if is_admin() else "否（无需）")
    print("  源目录     :", HERE)
    print("  安装目录   :", app)

    py = find_python()
    if not py:
        print("\n[错误] 找不到 Python 解释器，已尝试：")
        for p in _py_candidates():
            print("   ", p)
        print("     （以及 Windows 的 py 启动器）")
        _pause()
        return
    print("  Python     :", py)

    if not os.path.isfile(os.path.join(HERE, "guard.py")):
        print("\n[错误] 源目录里找不到 guard.py:", HERE)
        _pause()
        return

    cleanup_task()

    # --- 2. 复制到固定目录 ---
    print("\n[2/5] 复制运行文件到固定目录...")
    try:
        if os.path.abspath(HERE) == os.path.abspath(app):
            print("  [跳过] 已经在固定目录里运行")
        else:
            copied = stage(HERE, app)
            print("  [OK] 已复制:")
            for c in copied:
                print("      ", c)
    except Exception as e:
        print("  [错误] 复制失败:", e)
        _pause()
        return

    guard = os.path.join(app, "guard.py")
    watchdog = os.path.join(app, "watchdog.py")
    if not os.path.isfile(watchdog):
        print("  [错误] 目标目录缺少 watchdog.py:", watchdog)
        _pause()
        return

    # --- 3. 写启动文件夹 ---
    print("\n[3/5] 安装开机自启（启动文件夹）...")
    sd = startup_dir()
    if not sd or not os.path.isdir(sd):
        print("  [错误] 找不到启动文件夹:", sd)
        _pause()
        return
    try:
        path = make_launcher(app, py)
        print("  [OK]", path)
        print("       ->", watchdog)
    except Exception as e:
        print("  [错误] 写入失败:", e)
        _pause()
        return

    # --- 4. 启动 ---
    print("\n[4/5] 启动守护进程...")
    killed = stop_dir(HERE) + stop_dir(app)
    if killed:
        print("  已终止旧进程:", killed)
        time.sleep(0.8)
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        subprocess.Popen([py, watchdog], cwd=app,
                         creationflags=0x00000008 | 0x08000000,   # DETACHED | NO_WINDOW
                         startupinfo=si,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3.5)
        print("  已启动（看门狗 + guard）")
    except Exception as e:
        print("  [错误] 启动失败:", e)

    # --- 5. 验证 ---
    print("\n[5/5] 验证...")
    try:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run([_python_exe(py), guard, "--status"],
                           capture_output=True, encoding="utf-8",
                           errors="replace", timeout=30, env=env, cwd=app)
        for line in (r.stdout or "").splitlines():
            print("  " + line)
    except Exception as e:
        print("  验证失败:", repr(e))

    log = os.path.join(app, "guard.log")
    print("\n" + "=" * 62)
    print("  安装完成")
    print("=" * 62)
    print("""
  守护进程已在后台运行（guard v2），并且会：
    - 每次登录自动启动（静默、无窗口、无 UAC 弹窗）
    - 看门狗守护：进程退出或心跳停止（卡死）都会自动重启
    - 每次操作后用 Windows 音频 API 独立校验，不达标就回滚成"可用"
    - 连续失败 3 次自动熔断（guard.fused），转只观察，不再碰设备

  项目目录可以随意移动/重命名/删除 —— 自启指向的是固定目录，不受影响。

  体检     : "%s" "%s" --doctor
  看状态   : "%s" "%s" --status
  看端点   : "%s" "%s" --list
  只观察   : "%s" "%s" --monitor
  解除熔断 : "%s" "%s" --reset-fuse
  前台调试 : "%s" "%s" --debug
  自测     : "%s" "%s"        （在 tools\\ 里）
  日志     : %s
  卸载     : python install.py --uninstall [--purge]
""" % (_python_exe(py), guard, _python_exe(py), guard, _python_exe(py), guard,
       _python_exe(py), guard, _python_exe(py), guard, _python_exe(py), guard,
       _python_exe(py), os.path.join(app, "tools", "selftest.py"), log))

    _pause()


def do_uninstall():
    app = install_dir()
    purge = "--purge" in sys.argv
    print("=" * 62)
    print("  Edifier Guard - 卸载")
    print("=" * 62)
    print("  安装目录 :", app)

    killed = stop_dir(app) + stop_dir(HERE)
    print("  已停止进程:", killed or "（无）")

    sd = startup_dir()
    lp = os.path.join(sd, LAUNCHER) if sd else None
    if lp and os.path.isfile(lp):
        try:
            os.remove(lp)
            print("  [OK] 已移除启动器:", lp)
        except Exception as e:
            print("  [错误] 移除启动器失败:", e)
    else:
        print("  [跳过] 没有找到启动器")

    # 恢复音频端点
    guard = os.path.join(app, "guard.py")
    py = _python_exe(find_python())
    if py and os.path.isfile(guard):
        try:
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            subprocess.run([py, guard, "--restore"], capture_output=True,
                           encoding="utf-8", errors="replace", timeout=30,
                           env=env, cwd=app)
            print("  [OK] 已恢复音频端点为可用")
        except Exception as e:
            print("  [警告] 恢复失败:", e)

    if purge and os.path.isdir(app) and os.path.abspath(app) != os.path.abspath(HERE):
        try:
            shutil.rmtree(app)
            print("  [OK] 已删除安装目录:", app)
        except Exception as e:
            print("  [警告] 删除安装目录失败:", e)
    elif os.path.isdir(app):
        print("  安装目录保留（加 --purge 可一并删除）:", app)

    print("\n  完成。")
    _pause()


def main():
    if "--uninstall" in sys.argv or "--remove" in sys.argv:
        do_uninstall()
        return
    do_install()


if __name__ == "__main__":
    main()
