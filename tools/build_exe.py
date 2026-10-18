# -*- coding: utf-8 -*-
r"""把 EdifierGuard 打包成单文件 exe（PyInstaller onefile）。

用法:
    python tools/build_exe.py

产物:
    <仓库根>\EdifierGuard.exe      —— 单文件、免 Python、免任何外部路径依赖

要点:
  * 入口是 main.py（角色分派 + 控制台接管 + 自启/卸载）
  * 内嵌 hid 模块（lib/）与 hidapi.dll（放进解包目录；guard.py 会把它加进 DLL 搜索路径）
  * --noconsole：双击 / 自启不弹窗口；从命令行运行时 main.py 会把输出接回父控制台
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                         # 仓库根
ENTRY = os.path.join(ROOT, "main.py")
LIB = os.path.join(ROOT, "lib")
DLL = os.path.join(ROOT, "lib", "hid", "hidapi.dll")
NAME = "EdifierGuard"


def main():
    for p, what in ((ENTRY, "入口 main.py"), (DLL, "hidapi.dll")):
        if not os.path.isfile(p):
            print("找不到 %s: %s" % (what, p))
            return 2

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--noconsole",
        "--name", NAME,
        "--paths", LIB,
        "--hidden-import", "hid",
        "--hidden-import", "guard",
        "--hidden-import", "watchdog",
        "--hidden-import", "ipc",
        "--add-binary", DLL + os.pathsep + ".",
        "--distpath", ROOT,
        "--workpath", os.path.join(ROOT, "build", "pyi"),
        "--specpath", os.path.join(ROOT, "build", "pyi"),
        ENTRY,
    ]
    print("$ " + " ".join('"%s"' % a if " " in a else a for a in args))
    rc = subprocess.run(args, cwd=ROOT).returncode
    if rc != 0:
        print("\n打包失败 rc=%d" % rc)
        return rc

    exe = os.path.join(ROOT, NAME + ".exe")
    if os.path.isfile(exe):
        print("\n[OK] 产物: %s  (%.1f MB)" % (exe, os.path.getsize(exe) / 1048576.0))
        return 0
    print("没找到产物:", exe)
    return 1


if __name__ == "__main__":
    sys.exit(main())
