# -*- coding: utf-8 -*-
"""
Edifier G3 Max 底座版 —— 信号观察器（还原最初能工作的版本）

★ 核心：保持最初的读取方式 —— 纯被动 hid read，绝不主动 write。
   （我之前擅自加了主动轮询，反而把接收器搞乱了，这里已移除。）

用法：
    python watch.py

双击后会自动打开浏览器，实时显示所有收到的信号。
你不用管我，自己看哪些信号跟着耳机状态变就行。
"""

import os
import sys
import time
import json
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _bootstrap
    _ROOT = _bootstrap.ROOT
except Exception:
    _ROOT = os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else __file__))


def _dll():
    for d in (r"C:\Program Files\HECATE", _ROOT,
              os.path.join(_ROOT or "", "lib", "hid"),
              os.path.join(_ROOT or "", "runtime")):
        try:
            if d and os.path.isdir(d):
                os.add_dll_directory(d)
        except Exception:
            pass


_dll()

try:
    import hid
except Exception as e:
    print("无法加载 hid 库：%r" % (e,))
    input("按回车退出…")
    sys.exit(1)

VID, PID = 0x35BB, 0xA22B
PORT = 8737

# 帧结构（最初的脚本就是这样解出来的）：
#   [0]=0xD0  [1]=0x35  [2]=0xBB  [3]=op  [4..63]=60 字节载荷
OPS = {0x01: "FLAG", 0x07: "PEER-MAC", 0x09: "LED", 0x0A: "BATT/STATUS"}


class St:
    def __init__(self):
        self.lock = threading.Lock()
        self.started = time.time()
        self.interfaces = []
        self.frames = {}        # op -> {hex: count}
        self.last = {}          # op -> hex
        self.last_at = {}
        self.count = {}
        self.pkt_n = 0
        self.last_pkt = 0.0
        # 对 op=0A 的逐字节追踪
        self.b0a = {}
        self.b0a_at = {}
        self.log = []


S = St()


def addlog(msg):
    with S.lock:
        S.log.append({"t": time.strftime("%H:%M:%S"), "m": msg})
        if len(S.log) > 500:
            del S.log[:250]


def handle(b, label, now):
    op = b[3]
    pl = bytes(b[4:])
    h = pl.hex()

    with S.lock:
        S.pkt_n += 1
        S.last_pkt = now
        S.count[op] = S.count.get(op, 0) + 1
        S.frames.setdefault(op, {})
        S.frames[op][h] = S.frames[op].get(h, 0) + 1
        prev = S.last.get(op)
        S.last[op] = h
        S.last_at[op] = now

        if op == 0x0A:
            for i, v in enumerate(pl):
                if v:
                    S.b0a[i] = v
                    S.b0a_at[i] = now

    if prev != h:
        nz = [(i, "%02X" % v) for i, v in enumerate(pl) if v][:10]
        addlog("op=%02X %-11s 非零: %s" % (op, OPS.get(op, "?"), nz))


def reader(label, path):
    """★ 与原版完全一致的读取逻辑：只读，不写。"""
    try:
        dev = hid.Device(path=path)
        dev.nonblocking = 1
    except Exception as e:
        addlog("OPEN-FAIL %s %r" % (label, e))
        return

    addlog("OPENED %s" % label)
    while True:
        try:
            r = dev.read(64, timeout=20)          # ← 原版的超时值
            if r and any(r):
                handle(bytes(r), label, time.time())
        except Exception:
            pass
        time.sleep(0.01)                          # ← 原版的间隔


def snapshot():
    now = time.time()
    with S.lock:
        ops = {}
        for op in sorted(S.frames):
            fr = S.frames[op]
            top = sorted(fr.items(), key=lambda kv: -kv[1])[:6]
            ops["0x%02X" % op] = {
                "name": OPS.get(op, "?"),
                "count": S.count.get(op, 0),
                "age": (now - S.last_at[op]) if op in S.last_at else None,
                "variants": [{"hex": k[:40], "n": v} for k, v in top],
                "nvar": len(fr),
            }
        b0a = []
        for i in range(64):
            if i in S.b0a:
                b0a.append({"i": i, "v": S.b0a[i], "age": now - S.b0a_at[i]})
        return {
            "uptime": now - S.started,
            "pkt_n": S.pkt_n,
            "pkt_age": (now - S.last_pkt) if S.last_pkt else None,
            "ops": ops,
            "b0a": b0a,
            "log": S.log[-200:],
            "interfaces": S.interfaces,
        }


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Edifier G3 Max 信号观察器</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:"Microsoft YaHei",system-ui,sans-serif;background:#f4f6f9;color:#1b1f24}
header{padding:14px 20px 8px}
h1{margin:0 0 4px;font-size:18px}
.sub{color:#667085;font-size:12.5px;line-height:1.6}
.wrap{padding:0 20px 20px}
.bar{background:#fff;border:1px solid #e3e6ec;border-radius:8px;padding:8px 14px;
     margin:10px 0;font-size:12px;color:#475467;display:flex;gap:20px}
.bar b{color:#c0392b}
h2{font-size:13px;color:#344054;margin:16px 0 7px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e3e6ec;
      border-radius:8px;overflow:hidden;font-size:11.5px}
th{background:#f8f9fb;color:#475467;text-align:left;padding:6px 9px;
   border-bottom:1px solid #eef0f4;font-weight:700}
td{padding:5px 9px;border-bottom:1px solid #f4f5f7;font-family:Consolas,monospace}
tr:last-child td{border-bottom:none}
.hot{color:#c0392b;font-weight:700}
.dim{color:#98a2b3}
#log{background:#fff;border:1px solid #e3e6ec;border-radius:8px;height:260px;
     overflow:auto;padding:8px 12px;font-family:Consolas,monospace;font-size:11.5px}
#log div{padding:1.5px 0;color:#5f5e5a}
#log .t{color:#9aa4b2;margin-right:8px}
</style></head><body>
<header>
<h1>信号观察器（还原最初版本）</h1>
<div class="sub">纯被动读取，不发送任何数据。<b>打开 HECATE 驱动</b>才会出数据（驱动在轮询）。<br>
用耳机做动作，看哪一行跟着变。</div>
</header>
<div class="wrap">
<div class="bar">
  <span>运行 <b id="up">0</b> 秒</span>
  <span>报文 <b id="pn">0</b> 个</span>
  <span>距上一报文 <b id="pa">—</b></span>
  <span id="ifs" class="dim"></span>
</div>

<h2>① 各 op 命令（按收到的次数）</h2>
<table id="t1"><tr><th>op</th><th>名称</th><th>次数</th><th>载荷变化种数</th><th>最近</th></tr></table>

<h2>② op=0x0A 出现过的非零字节（★ 重点看这里）</h2>
<table id="t2"><tr><th>字节偏移</th><th>值</th><th>最近更新</th></tr></table>

<h2>③ 载荷明细（每种唯一载荷及其次数）</h2>
<table id="t3"><tr><th>op</th><th>载荷（前40字符）</th><th>次数</th></tr></table>

<h2>④ 变化日志</h2>
<div id="log"></div>
</div>
<script>
function age(a){if(a===null||a===undefined)return'从未';if(a<1)return'刚刚';
  if(a<60)return a.toFixed(0)+'秒前';return(a/60).toFixed(1)+'分前';}
let seenLog=0;
async function tick(){
 try{
  const s=await(await fetch('/api/state',{cache:'no-store'})).json();
  document.getElementById('up').textContent=s.uptime.toFixed(0);
  document.getElementById('pn').textContent=s.pkt_n;
  document.getElementById('pa').textContent=age(s.pkt_age);
  document.getElementById('ifs').textContent='接口: '+(s.interfaces||[]).join(',');

  let a='<tr><th>op</th><th>名称</th><th>次数</th><th>载荷变化种数</th><th>最近</th></tr>';
  for(const[k,v]of Object.entries(s.ops||{})){
    a+=`<tr><td class="hot">${k}</td><td style="font-family:inherit">${v.name}</td>
        <td>${v.count}</td><td>${v.nvar}</td>
        <td class="${v.age<10?'hot':'dim'}">${age(v.age)}</td></tr>`;
  }
  document.getElementById('t1').innerHTML=a;

  let b='<tr><th>字节偏移</th><th>值</th><th>最近更新</th></tr>';
  for(const x of (s.b0a||[])){
    const hot = x.age<15;
    b+=`<tr><td class="${hot?'hot':'dim'}">byte[${x.i}]</td>
        <td class="${hot?'hot':'dim'}">0x${x.v.toString(16).toUpperCase().padStart(2,'0')} (${x.v})</td>
        <td class="${hot?'hot':'dim'}">${age(x.age)}</td></tr>`;
  }
  document.getElementById('t2').innerHTML=b;

  let c='<tr><th>op</th><th>载荷（前40字符）</th><th>次数</th></tr>';
  for(const[k,v]of Object.entries(s.ops||{})){
    for(const va of v.variants){
      c+=`<tr><td>${k}</td><td style="font-size:10.5px">${va.hex}</td><td>${va.n}</td></tr>`;
    }
  }
  document.getElementById('t3').innerHTML=c;

  const lg=document.getElementById('log');
  if(s.log.length!==seenLog){
    seenLog=s.log.length;
    lg.innerHTML=s.log.map(e=>`<div><span class="t">${e.t}</span>${e.m}</div>`).join('');
    lg.scrollTop=lg.scrollHeight;
  }
 }catch(e){}
 setTimeout(tick,400);
}
tick();
</script></body></html>
"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api/state"):
            body = json.dumps(snapshot(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def main():
    targets = []
    for d in hid.enumerate():
        if d.get("vendor_id") == VID and d.get("product_id") == PID:
            p = d.get("path", b"")
            if isinstance(p, bytes):
                p = p.decode("utf-8", "ignore")
            for c in ("Col01", "Col02", "Col03", "Col04"):
                if c in p:
                    targets.append((c, d["path"]))
    targets.sort()
    with S.lock:
        S.interfaces = [t[0] for t in targets]

    if not targets:
        print("=" * 56)
        print(" 没找到接收器 %04X:%04X" % (VID, PID))
        print(" 请确认 USB 接收器已插好。")
        print("=" * 56)
        input("按回车退出…")
        return

    for label, path in targets:
        threading.Thread(target=reader, args=(label, path), daemon=True).start()

    port = PORT
    while True:
        sk = socket.socket()
        try:
            sk.bind(("127.0.0.1", port))
            sk.close()
            break
        except OSError:
            sk.close()
            port += 1

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = "http://127.0.0.1:%d/" % port
    print("=" * 56)
    print(" 观察器已启动：%s" % url)
    print(" 接口：%s" % ", ".join(t[0] for t in targets))
    print(" 提示：请打开 HECATE 驱动软件，否则可能收不到数据。")
    print(" 关闭本窗口即可停止。")
    print("=" * 56)
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
