# -*- coding: utf-8 -*-
"""
Edifier Guard 自测 / 故障注入（selftest.py）

为什么需要这个文件
------------------
v1 的验收只测了"正常路径"：端点正常时开关一下、看设备跟着变。
结果端点一旦被推到 NOTPRESENT(4)，程序就卡死在"自认为成功"，
用户耳机彻底消失 —— 而验收完全没发现。

所以验收必须包含"故意把状态弄坏，看能不能救回来"。
本脚本就是这件事的自动化版本。

用法
----
    python selftest.py           只跑纯逻辑用例（不改任何设备，绝对安全）
    python selftest.py --inject  追加真实故障注入（会临时改动 HECATE 端点，
                                  结束时无论成败都会恢复正常）

退出码 0 = 全通过；1 = 有失败
"""
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bootstrap  # noqa: F401  # 把「项目根」加入 sys.path
HERE = _bootstrap.ROOT or os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import guard as G

INJECT = "--inject" in sys.argv
_results = []


def check(name, cond, detail=""):
    _results.append((bool(cond), name, detail))
    print("  %s %s%s" % ("✓ PASS" if cond else "✗ FAIL", name,
                        ("   " + detail) if detail else ""))


class StubAPI:
    """假的音频 API：用于纯逻辑用例，不碰真实系统"""
    ok = True

    def __init__(self, active=None, hr=0):
        self._active = active or {0: set(), 1: set()}
        self._hr = hr

    def active_ids(self):
        return self._active

    def activate_hr(self, devid):
        return self._hr

    def close(self):
        pass


def fake_ep(**kw):
    e = dict(kind="Render", guid="{00000000-0000-0000-0000-000000000000}",
             name="HECATE TEST", devid="{0.0.0.00000000}.{00000000-0000-0000-0000-000000000000}",
             state=G.STATE_ACTIVE, devinst="USB\\VID_35BB&PID_A22B&MI_00\\TEST",
             pnp_present=True, pnp_problem=0, operable=True, usable=True, live=True)
    e.update(kw)
    return e


# ================= 纯逻辑用例（无副作用） =================
def unit_tests():
    print("\n[1] 纯逻辑用例（不改动任何设备）")

    # --- 假成功回归：空集合必须返回 False ---
    orig = G.targets_classified
    try:
        G.targets_classified = lambda: [
            fake_ep(pnp_present=False, pnp_problem=None, operable=False,
                    usable=False, live=False, state=G.STATE_NOTPRESENT)]
        ok, why = G.apply_visible(True, allow_repair=False)
        check("空集合 -> 必须返回 False（v1 的假成功 bug）",
              ok is False and why == "no-present-endpoint", "实际=%s/%s" % (ok, why))

        G.targets_classified = lambda: []
        ok, why = G.apply_visible(True, allow_repair=False)
        check("无目标端点 -> 必须返回 False", ok is False, "实际=%s/%s" % (ok, why))
    finally:
        G.targets_classified = orig

    # --- 隐藏后落到 NOTPRESENT 必须判失败（最危险的回归）---
    ep = fake_ep()
    fresh = {ep["devid"]: fake_ep(state=G.STATE_NOTPRESENT)}
    ok, why, _ = G._check(ep, False, {0: set(), 1: set()}, fresh, StubAPI())
    check("隐藏后变成 NOTPRESENT -> 判失败并准备回滚",
          ok is False and why == "hide-became-notpresent", "实际=%s/%s" % (ok, why))

    # --- 正常隐藏：DISABLED 且不在活跃列表 ---
    fresh = {ep["devid"]: fake_ep(state=G.STATE_DISABLED)}
    ok, why, _ = G._check(ep, False, {0: set(), 1: set()}, fresh, StubAPI())
    check("隐藏后 DISABLED -> 判成功", ok is True and why is None, "实际=%s/%s" % (ok, why))

    # --- 隐藏没生效（还在活跃列表）-> 判失败 ---
    ok, why, _ = G._check(ep, False, {0: {ep["devid"].lower()}, 1: set()},
                          {ep["devid"]: fake_ep(state=G.STATE_ACTIVE)}, StubAPI())
    check("隐藏未生效（仍在活跃列表）-> 判失败",
          ok is False and why == "still-active", "实际=%s/%s" % (ok, why))

    # --- PnP 禁用时必须报 pnp-problem-22，而不是假装成功 ---
    fresh = {ep["devid"]: fake_ep(state=G.STATE_ACTIVE, pnp_problem=22)}
    ok, why, _ = G._check(ep, True, {0: set(), 1: set()}, fresh, StubAPI())
    check("底层被禁用 -> 报 pnp-problem-22",
          ok is False and why == "pnp-problem-22", "实际=%s/%s" % (ok, why))

    # --- 僵尸端点（在列表里但 Activate 失败）必须被识别 ---
    ok, why, hr = G._check(ep, True, {0: {ep["devid"].lower()}, 1: set()},
                           {ep["devid"]: fake_ep()},
                           StubAPI(hr=G.HR_PATH_NOT_FOUND))
    check("Activate 失败 0x80070003 -> 识别为僵尸端点",
          ok is False and "zombie" in (why or ""), "实际=%s/%s" % (ok, why))

    # --- 正常可用 ---
    ok, why, hr = G._check(ep, True, {0: {ep["devid"].lower()}, 1: set()},
                           {ep["devid"]: fake_ep()}, StubAPI(hr=0))
    check("活跃且 Activate 成功 -> 判成功", ok is True, "hr=0x%08X" % (hr or 0))

    # --- 熔断：连续失败必须写标记并转只观察 ---
    fuse_backup = None
    if os.path.isfile(G.FUSE_FILE):
        with open(G.FUSE_FILE, "r", encoding="utf-8") as f:
            fuse_backup = f.read()
    try:
        G.fuse_on("selftest")
        check("熔断标记可写入", G.fuse_present())
        check("熔断标记可解除", G.fuse_off() and not G.fuse_present())
    finally:
        if fuse_backup is not None:
            with open(G.FUSE_FILE, "w", encoding="utf-8") as f:
                f.write(fuse_backup)


# ================= 真实故障注入 =================
def live_eps():
    return [e for e in G.targets_classified() if e["operable"]]


def inject_tests():
    print("\n[2] 真实故障注入（会临时改动端点，结束自动恢复）")
    eps = live_eps()
    if not eps:
        check("存在可注入的端点（接收器需插着）", False, "没有底层在场的 HECATE 端点")
        return

    try:
        # --- A. 端点被推到 NOTPRESENT：v1 就是卡在这里 ---
        for e in eps:
            G.write_devicestate(e, G.STATE_NOTPRESENT)
        time.sleep(0.4)
        check("注入 NOTPRESENT 成功（模拟 v1 卡死现场）",
              G.current_state() in ("absent", "mixed", "hidden"),
              "state=%s" % G.current_state())

        ok, why = G.apply_visible(True)
        check("★ 自愈：NOTPRESENT -> 可见", ok, "%s / %s" % (ok, why))

        # --- B. 端点被禁用 ---
        for e in eps:
            G.write_devicestate(e, G.STATE_DISABLED)
        time.sleep(0.4)
        ok, why = G.apply_visible(True)
        check("★ 自愈：DISABLED -> 可见", ok, "%s / %s" % (ok, why))

        # --- C. 正常隐藏，必须落到 DISABLED 而不是 NOTPRESENT ---
        ok, why = G.apply_visible(False)
        st = [e["state"] for e in G.targets_classified() if e["operable"]]
        check("隐藏成功且状态为 DISABLED（不是 NOTPRESENT）",
              ok and st and all(s == G.STATE_DISABLED for s in st),
              "ok=%s detail=%s states=%s" % (ok, why, [G.state_name(s) for s in st]))

        # --- D. 假成功回归：让 SetEndpointVisibility 变成空操作 ---
        #     此时隐藏状态未被改变，apply_visible(True) 必须返回 False 而不是 True
        orig_get_policy = G._get_policy

        class NoopPolicy:
            ok = True

            def set_visible(self, devid, visible):
                return True          # 谎报成功

            def close(self):
                pass

        try:
            G._get_policy = lambda: NoopPolicy()
            ok, why = G.apply_visible(True, allow_repair=False)
            check("★ 假成功回归：接口谎报成功时能被校验抓住",
                  ok is False, "实际=%s / %s" % (ok, why))
        finally:
            G._get_policy = orig_get_policy

        # --- E. 恢复 ---
        ok, why = G.apply_visible(True)
        check("恢复为可见", ok and G.current_state() == "visible",
              "%s / %s / state=%s" % (ok, why, G.current_state()))
    finally:
        # 无论成败，收尾一定恢复可用
        try:
            G.restore_all()
        except Exception as e:
            print("  (收尾恢复异常: %s)" % e)


def main():
    print("=" * 68)
    print("  Edifier Guard 自测   v%s   模式=%s"
          % (G.VERSION, "逻辑 + 真实注入" if INJECT else "仅逻辑（不改设备）"))
    print("=" * 68)

    unit_tests()
    if INJECT:
        inject_tests()
    else:
        print("\n[2] 真实故障注入 …… 跳过（加 --inject 才执行）")

    fails = [r for r in _results if not r[0]]
    print("\n" + "=" * 68)
    print("  结果：%d 项通过 / %d 项失败" % (len(_results) - len(fails), len(fails)))
    for ok, name, detail in fails:
        print("    ✗ %s  %s" % (name, detail))
    print("=" * 68)

    print("\n  当前设备状态：%s" % G.current_state())
    return 0 if not fails else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        try:
            G.restore_all()
        except Exception:
            pass
        sys.exit(2)
