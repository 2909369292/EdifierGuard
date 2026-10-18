# -*- coding: utf-8 -*-
"""EdifierGuard 的「全内存」进程间状态 —— **运行期不落任何文件**。

用户要求：exe 复制到任何一台电脑都能直接跑，不需要任何路径 / 配置，
正常使用时**不产生日志、配置、pid、心跳等任何文件**。

所以 guard.py 与 watchdog.py 之间的协作全部走内存：

* **共享内存**（Windows file mapping，具名）——
  心跳、两个进程的 PID、熔断标记、当前模式、动作结果；
* **具名互斥体**（见 watchdog.py / guard.py）——单实例。

名字用 ``Local\\`` 命名空间：guard 由 watchdog 拉起，天然在同一登录会话，
不需要 ``Global\\``（在标准用户下创建 Global 对象还可能因权限失败）。

这一块内存是「易失」的，重启即清空 —— 正合所需：
熔断标记不应该跨重启赖着不走（启动时本来就会先自愈、恢复端点）。
"""
import ctypes
import ctypes.wintypes as wt

# ---------------------------------------------------------------- 布局
NAME = "Local\\EdifierGuard.State.v2"
SIZE = 64
MAGIC = 0x31474445                      # 'EDG1'（小端）

OFF_MAGIC     = 0      # u32  魔数（用来判断"是不是刚建出来的空白内存"）
OFF_WD_PID    = 4      # u32  看门狗 PID
OFF_GUARD_PID = 8      # u32  守护 PID
OFF_HB_TICK   = 16     # u64  guard 周期写入 GetTickCount64()
OFF_HB_SEQ    = 24     # u64  每次心跳 +1（用于观察是否真的在跳）
OFF_FUSED     = 32     # u32  1 = 已熔断（只观察）
OFF_MODE      = 36     # u32  0 = monitor，1 = enforce
OFF_STATE     = 40     # u32  0 = unknown，1 = visible，2 = hidden
OFF_LAST_OK   = 44     # u32  0 = n/a，1 = ok，2 = fail
OFF_APPLY_OK  = 48     # u32  累计成功动作次数
OFF_FAIL_RUN  = 52     # u32  当前连续失败次数

PAGE_READWRITE = 0x04
FILE_MAP_ALL_ACCESS = 0x000F001F

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetTickCount64.restype = ctypes.c_ulonglong
_k32.GetTickCount64.argtypes = []
_k32.CreateFileMappingW.restype = wt.HANDLE
_k32.CreateFileMappingW.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                                    wt.DWORD, wt.DWORD, wt.LPCWSTR]
_k32.OpenFileMappingW.restype = wt.HANDLE
_k32.OpenFileMappingW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD,
                               ctypes.c_size_t]
_k32.UnmapViewOfFile.restype = wt.BOOL
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.restype = wt.BOOL
_k32.CloseHandle.argtypes = [wt.HANDLE]

_ALIVE = []                              # 持有句柄，防止被回收


def tick():
    """系统启动以来的毫秒数。同一台机器上所有进程一致。"""
    try:
        return int(_k32.GetTickCount64())
    except Exception:
        return 0


class State(object):
    """共享内存的一个视图。"""

    def __init__(self, view, handle):
        self._view = int(view)
        self._handle = handle

    # ---- 基础读写 ----
    def u32(self, off):
        return ctypes.c_uint32.from_address(self._view + off).value

    def set_u32(self, off, value):
        ctypes.c_uint32.from_address(self._view + off).value = int(value) & 0xFFFFFFFF

    def u64(self, off):
        return ctypes.c_uint64.from_address(self._view + off).value

    def set_u64(self, off, value):
        ctypes.c_uint64.from_address(self._view + off).value = int(value) & 0xFFFFFFFFFFFFFFFF

    def clear(self):
        for off in range(0, SIZE, 4):
            self.set_u32(off, 0)

    # ---- 语义便捷方法 ----
    def beat(self):
        """guard 的心跳。"""
        self.set_u64(OFF_HB_TICK, tick())
        self.set_u64(OFF_HB_SEQ, self.u64(OFF_HB_SEQ) + 1)

    def age_ms(self):
        """距离上一次心跳过了多少毫秒（0 表示还没跳过）。"""
        last = self.u64(OFF_HB_TICK)
        if not last:
            return None
        return max(0, tick() - last)

    def close(self):
        try:
            _k32.UnmapViewOfFile(ctypes.c_void_p(self._view))
        except Exception:
            pass
        try:
            _k32.CloseHandle(self._handle)
        except Exception:
            pass


def _map(handle, fresh_ok):
    p = _k32.MapViewOfFile(handle, FILE_MAP_ALL_ACCESS, 0, 0, SIZE)
    if not p:
        _k32.CloseHandle(handle)
        return None
    st = State(p, handle)
    if fresh_ok and st.u32(OFF_MAGIC) != MAGIC:
        st.clear()
        st.set_u32(OFF_MAGIC, MAGIC)
    _ALIVE.append(st)
    return st


def create():
    """创建（或打开）共享状态。失败返回 None。"""
    try:
        h = _k32.CreateFileMappingW(ctypes.c_void_p(-1), None, PAGE_READWRITE,
                                    0, SIZE, NAME)
        if not h:
            return None
    except Exception:
        return None
    return _map(h, True)


def open():
    """只打开「已经存在」的共享状态（没有就返回 None）。"""
    try:
        h = _k32.OpenFileMappingW(FILE_MAP_ALL_ACCESS, False, NAME)
        if not h:
            return None
    except Exception:
        return None
    return _map(h, False)


def attach():
    """打开已有的；没有就创建。"""
    return open() or create()


def running_pids():
    """取出 (看门狗 PID, 守护 PID)；没在跑就返回 (0, 0)。"""
    st = open()
    if st is None:
        return 0, 0
    try:
        return st.u32(OFF_WD_PID), st.u32(OFF_GUARD_PID)
    finally:
        st.close()
