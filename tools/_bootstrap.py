# -*- coding: utf-8 -*-
"""定位「项目根」并把依赖挂好，供 tools/ 下的脚本复用。

项目根 = 从本文件所在目录**向上查找**包含 ``guard.py`` 的目录。
这样：
  - 把 tools/ 放到任何层级都不影响；
  - 整个项目搬到任意路径（甚至被安装到 %LOCALAPPDATA%）都不用改代码；
  - 不依赖“当前工作目录”，双击/计划任务/任意位置启动都成立。

用法（tools/ 下的脚本）：
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _bootstrap          # 副作用：把项目根加入 sys.path，并挂好 DLL 目录
    ROOT = _bootstrap.ROOT
"""
import os
import sys


def find_root(start=None):
    """向上查找包含 guard.py 的目录；找不到返回 None。"""
    p = os.path.abspath(start or os.path.dirname(os.path.abspath(__file__)))
    for _ in range(8):
        if os.path.isfile(os.path.join(p, "guard.py")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return None


ROOT = find_root()

if ROOT:
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    for _d in (ROOT,
               os.path.join(ROOT, "lib"),
               os.path.join(ROOT, "lib", "hid"),
               os.path.join(ROOT, "runtime")):
        try:
            if os.path.isdir(_d):
                os.add_dll_directory(_d)
        except Exception:
            pass
