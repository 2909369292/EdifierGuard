# -*- coding: utf-8 -*-
"""
Edifier G3 Max 底座版 —— 信号观察器（增强版）

★ 基线：纯被动读取 + 电量判定（已验证可用，这两点保持不变）
★ 新增：
    - 更多信号展示（逐字节、每个 op 的全部载荷变体）
    - 主动轮询模式（可选，默认关闭）—— 驱动不开时也能拿数据
    - 判定依据可视化（每个信号的当前值 + 是否跟随状态）

用法：
    python gui_server.py              被动模式（默认，和验证过的一致）
    python gui_server.py --poll       主动轮询模式

纯被动模式下不发送任何指令。
"""

import os
import sys
import json
import time
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


def _add_dll_dirs():
    for d in (r"C:\Program Files\HECATE", _ROOT,
              os.path.join(_ROOT or "", "lib", "hid"),
              os.path.join(_ROOT or "", "runtime")):
        try:
            if d and os.path.isdir(d):
                os.add_dll_directory(d)
        except Exception:
            pass


_add_dll_dirs()

try:
    import hid
except Exception as e:
    print("无法加载 hid 库：%r" % (e,))
    input("按回车退出…")
    sys.exit(1)

VID = 0x35BB
PID = 0xA22B
PORT = 8731

# 主动轮询模式（默认关；--poll 打开）
USE_POLL = "--poll" in sys.argv

OPS = {
    0x01: "FLAG 状态位",
    0x02: "未知02",
    0x03: "未知03",
    0x04: "未知04",
    0x05: "未知05",
    0x06: "未知06",
    0x07: "PEER-MAC 对端地址",
    0x08: "未知08",
    0x09: "LED 状态",
    0x0A: "BATT 电量/状态",
    0x0B: "未知0B",
    0x0C: "未知0C",
}


class St:
    def __init__(self):
        self.lock = threading.Lock()
        self.started = time.time()
        self.interfaces = []
        # 核心信号
        self.battery = None
        self.battery_at = 0.0
        self.mac = None
        self.mac_at = 0.0
        self.mac_n = 0
        self.flag = None
        self.flag_at = 0.0
        # 报文
        self.pkt_n = 0
        self.last_pkt = 0.0
        # 逐 op 统计
        self.frames = {}        # op -> {hex: count}
        self.op_last = {}       # op -> hex
        self.op_at = {}         # op -> ts
        self.op_n = {}          # op -> count
        # op=0A 逐字节
        self.b0a = {}           # idx -> value
        self.b0a_at = {}        # idx -> ts
        self.b0a_seen = {}      # idx -> set(values)
        # 轮询
        self.poll_n = 0
        # 日志
        self.events = []


S = St()


def note(msg, kind="info"):
    with S.lock:
        S.events.append({"t": time.strftime("%H:%M:%S"), "ts": time.time(),
                         "msg": msg, "kind": kind})
        if len(S.events) > 600:
            del S.events[:300]


def on_frame(b, label, now):
    op = b[3]
    pl = bytes(b[4:])
    h = pl.hex()

    with S.lock:
        S.pkt_n += 1
        S.last_pkt = now
        S.frames.setdefault(op, {})
        S.frames[op][h] = S.frames[op].get(h, 0) + 1
        prev = S.op_last.get(op)
        S.op_last[op] = h
        S.op_at[op] = now
        S.op_n[op] = S.op_n.get(op, 0) + 1

        # 逐字节记录 op=0A（电量所在）
        if op == 0x0A:
            for i, v in enumerate(pl):
                S.b0a_seen.setdefault(i, set()).add(v)
                if v:
                    S.b0a[i] = v
                    S.b0a_at[i] = now

    # ---- 核心信号提取 ----
    if op == 0x07 and len(pl) >= 7 and pl[0] == 0x02:
        mac = ":".join("%02X" % x for x in pl[1:7])
        with S.lock:
            ch = (S.mac != mac)
            S.mac = mac
            S.mac_at = now
            S.mac_n += 1
        if ch:
            note("op=07 对端 MAC = %s" % mac, "hit")

    elif op == 0x0A:
        # 电量：payload[2]（兼容旧的 word[1] 解读）
        bat = pl[2] if len(pl) > 2 else 0
        if bat == 0 and len(pl) > 3:
            bat = pl[3]
        with S.lock:
            old = S.battery
            S.battery = bat
            S.battery_at = now
        if old != bat:
            note("op=0A 电量 = %s" % bat, "hit")

    elif op == 0x01:
        val = pl[0] if pl else 0
        with S.lock:
            old = S.flag
            S.flag = val
            S.flag_at = now
        if old != val:
            note("op=01 FLAG = 0x%02X" % val, "hit")

    elif prev != h:
        nz = [(i, "%02X" % v) for i, v in enumerate(pl) if v][:10]
        note("op=%02X %s 非零: %s" % (op, OPS.get(op, "?"), nz), "hit")


def reader(label, path):
    """
    读取循环。
    被动模式：只 read（保持与验证过的版本完全一致）
    轮询模式：先 write 一帧查询，再 read

    注意：只有 Col03 接口支持写入，其余 Col01/02/04 是只读的，
          对它们发查询会报 WriteFile 错误 —— 所以那些接口保持纯被动。
    """
    try:
        dev = hid.Device(path=path)
        dev.nonblocking = 1
    except Exception as e:
        note("[%s] 打开失败 %r" % (label, e), "err")
        return

    # 只有 Col03 能写
    can_poll = USE_POLL and (label == "Col03")
    note("[%s] 开始监听%s" % (label, "（主动轮询）" if can_poll else "（被动）"), "ok")

    req = bytes([0xD0, 0x35, 0xBB, 0x01]) + bytes(60)
    last_req = 0.0
    poll_err = 0

    while True:
        if can_poll:
            now = time.time()
            if now - last_req >= 1.5:
                try:
                    dev.write(req)
                    last_req = now
                    with S.lock:
                        S.poll_n += 1
                except Exception as e:
                    poll_err += 1
                    if poll_err <= 2:       # 只报前两次，避免刷屏
                        note("[%s] 查询发送失败 %r" % (label, e), "err")
                    elif poll_err == 3:
                        note("[%s] 停止轮询（该接口不支持写入）" % label, "err")
                        can_poll = False
                    time.sleep(0.3)

        try:
            r = dev.read(64, timeout=15)
            if r and any(r):
                if len(r) >= 5:
                    on_frame(bytes(r), label, time.time())
        except Exception:
            pass
        time.sleep(0.003)


def snapshot():
    now = time.time()
    with S.lock:
        bat_age = (now - S.battery_at) if S.battery_at else None
        pkt_age = (now - S.last_pkt) if S.last_pkt else None

        # ★ 判定：以电量为主（已验证）
        if S.battery is None:
            verdict, why = "unknown", "还没收到电量数据"
        elif S.battery > 0:
            verdict, why = "online", "电量 = %d%%" % S.battery
        else:
            verdict, why = "offline", "电量 = 0（在底座充电 / 已关机）"

        # 逐 op 表
        ops = {}
        for op in sorted(S.frames):
            fr = S.frames[op]
            top = sorted(fr.items(), key=lambda kv: -kv[1])[:5]
            ops["0x%02X" % op] = {
                "name": OPS.get(op, "未知"),
                "n": S.op_n.get(op, 0),
                "nvar": len(fr),
                "age": (now - S.op_at[op]) if op in S.op_at else None,
                "variants": [{"hex": k[:44], "n": v} for k, v in top],
            }

        # op=0A 逐字节
        b0a = []
        for i in sorted(S.b0a_seen):
            vals = S.b0a_seen[i]
            b0a.append({
                "i": i,
                "v": S.b0a.get(i),
                "age": (now - S.b0a_at[i]) if i in S.b0a_at else None,
                "nvar": len(vals),
                "vals": sorted(vals)[:6],
            })

        return {
            "uptime": now - S.started,
            "verdict": verdict, "why": why,
            "battery": S.battery, "battery_age": bat_age,
            "mac": S.mac, "mac_age": (now - S.mac_at) if S.mac_at else None,
            "mac_n": S.mac_n,
            "flag": S.flag, "flag_age": (now - S.flag_at) if S.flag_at else None,
            "pkt_n": S.pkt_n, "pkt_age": pkt_age,
            "poll_mode": USE_POLL, "poll_n": S.poll_n,
            "ops": ops, "b0a": b0a,
            "events": S.events[-240:],
            "interfaces": S.interfaces,
        }


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Edifier G3 Max — 信号观察器</title>
<style>
  *{box-sizing:border-box}
  body{margin:0;font-family:"Microsoft YaHei","Segoe UI",sans-serif;
       background:#f4f6f9;color:#1b1f24}
  header{padding:14px 20px 8px}
  h1{margin:0 0 4px;font-size:19px}
  .sub{color:#667085;font-size:12.5px;line-height:1.65}
  .wrap{padding:0 20px 18px}

  .verdict{display:flex;align-items:center;gap:12px;padding:12px 16px;
           border-radius:10px;border:1px solid #e3e6ec;background:#fff;margin-top:12px}
  .verdict .vd-dot{width:12px;height:12px;border-radius:50%;background:#98a2b3;flex:none}
  .verdict .vd-txt b{display:block;font-size:16px}
  .verdict .vd-txt span{font-size:11.5px;color:#667085}
  .verdict .vd-hint{margin-left:auto;font-size:11px;color:#98a2b3;text-align:right}
  .verdict.online{background:#f0fdf4;border-color:#a6f4c5}
  .verdict.online .vd-dot{background:#12b76a;box-shadow:0 0 0 4px rgba(18,183,106,.18)}
  .verdict.online .vd-txt b{color:#067647}
  .verdict.offline{background:#fef3f2;border-color:#fecdca}
  .verdict.offline .vd-dot{background:#f04438;box-shadow:0 0 0 4px rgba(240,68,56,.16)}
  .verdict.offline .vd-txt b{color:#b42318}

  .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:12px 0 6px}
  .card{background:#fff;border:1px solid #e3e6ec;border-radius:10px;padding:10px 12px 11px}
  .card.live{border-color:#e05d4f;box-shadow:0 0 0 3px rgba(224,93,79,.13)}
  .card .t{font-size:11.5px;font-weight:700;color:#344054}
  .card .big{font-family:Consolas,monospace;font-size:20px;font-weight:700;
             color:#98a2b3;margin:4px 0 2px;word-break:break-all;line-height:1.15}
  .card.live .big{color:#c0392b}
  .card .d{font-size:10.5px;color:#98a2b3;line-height:1.5}

  h2{font-size:13px;color:#344054;margin:16px 0 7px}
  table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e3e6ec;
        border-radius:8px;overflow:hidden;font-size:11.5px}
  th{background:#f8f9fb;color:#475467;text-align:left;padding:6px 9px;
     border-bottom:1px solid #eef0f4;font-weight:700}
  td{padding:5px 9px;border-bottom:1px solid #f4f5f7;font-family:Consolas,monospace}
  tr:last-child td{border-bottom:none}
  .hot{color:#c0392b;font-weight:700}
  .dim{color:#98a2b3}
  .pill{display:inline-block;padding:1px 7px;border-radius:9px;font-size:10px;
        background:#eef0f4;color:#667085;font-family:"Microsoft YaHei"}
  .pill.v{background:#fee4e2;color:#b42318}

  .logs{background:#fff;border:1px solid #e3e6ec;border-radius:10px;margin-top:14px}
  .logs h3{margin:0;padding:9px 14px;font-size:12.5px;color:#344054;
           border-bottom:1px solid #eef0f4}
  #log{font-family:Consolas,monospace;font-size:11.5px;height:200px;
       overflow:auto;padding:8px 14px}
  #log div{padding:1.5px 0;color:#8b95a5}
  #log div.hit{color:#c0392b;font-weight:700}
  #log div.err{color:#b54708;font-weight:700}
  #log div.ok{color:#067647}
  #log .t{color:#9aa4b2;margin-right:8px}
  footer{padding:6px 20px 14px;font-size:11px;color:#98a2b3}
</style>
</head>
<body>
<header>
  <h1>信号观察器 <span id="mode" class="pill">被动</span></h1>
  <div class="sub">
    用耳机做动作：<b>放到底座上</b> → 等几秒 → <b>拿起来</b>。<br>
    判定依据：<b>电量</b>（已验证）。表格里标 <span class="pill v">会变</span> 的字节
    就是跟随状态的信号。
  </div>
</header>
<div class="wrap">
  <div id="verdict" class="verdict unknown">
    <div class="vd-dot"></div>
    <div class="vd-txt"><b id="vd-main">等待数据…</b><span id="vd-why"></span></div>
    <div class="vd-hint" id="vd-hint">判定依据：op=0A 电量</div>
  </div>

  <h2>① 核心信号</h2>
  <div class="grid" id="grid"></div>

  <h2>② op=0x0A 逐字节（★ 电量在这里）</h2>
  <table id="t0a">
    <tr><th>偏移</th><th>当前值</th><th>历史值</th><th>是否变过</th><th>最近</th></tr>
  </table>

  <h2>③ 所有 op 命令及其载荷</h2>
  <table id="top">
    <tr><th>op</th><th>名称</th><th>次数</th><th>载荷种数</th><th>最近</th></tr>
  </table>

  <h2>④ 载荷明细</h2>
  <table id="tpl">
    <tr><th>op</th><th>载荷（前44字符）</th><th>次数</th></tr>
  </table>

  <h2>⑤ 变化日志</h2>
  <div class="logs"><div id="log"></div></div>
</div>
<footer id="ft">连接中…</footer>

<script>
const CARDS = [
  { key:'bat',  t:'① 电量 op=0A',   d:'有值=在线；0=在底座' },
  { key:'mac',  t:'② 对端 MAC',     d:'出现=已建立链路' },
  { key:'flag', t:'③ FLAG op=01',   d:'状态标志位' },
  { key:'pkt',  t:'④ 报文上报',     d:'距上一报文' },
  { key:'macn', t:'⑤ MAC 累计',     d:'累计次数' },
  { key:'up',   t:'⑥ 运行时长',     d:'本次监控' },
];
const grid=document.getElementById('grid');
const els={};
for(const c of CARDS){
  const d=document.createElement('div');
  d.className='card';
  d.innerHTML=`<div class="t">${c.t}</div><div class="big">—</div><div class="d">${c.d}</div>`;
  grid.appendChild(d);
  els[c.key]={root:d,big:d.querySelector('.big'),sub:d.querySelector('.d'),base:c.d};
}
const logEl=document.getElementById('log');
let seen=0;

function age(a){if(a===null||a===undefined)return'从未';if(a<1)return'刚刚';
  if(a<60)return a.toFixed(0)+'秒前';return(a/60).toFixed(1)+'分前';}
function hx(v){return v===null||v===undefined?'—':'0x'+v.toString(16).toUpperCase().padStart(2,'0');}
function paint(k,text,live,sub){const e=els[k];e.big.textContent=text;
  e.root.classList.toggle('live',!!live);e.sub.textContent=sub||e.base;}

async function tick(){
 try{
  const s=await(await fetch('/api/state',{cache:'no-store'})).json();

  if(s.battery===null||s.battery===undefined){paint('bat','—',false,'还未收到');}
  else if(s.battery>0){paint('bat',s.battery+'%',true,'耳机在线');}
  else{paint('bat','0',false,'在底座 / 已关机');}
  paint('mac',s.mac||'未出现',!!(s.mac&&s.mac_age<20),
        s.mac?('最近 '+age(s.mac_age)):'至今未收到');
  paint('flag',hx(s.flag),false,s.flag_age!==null?('最近 '+age(s.flag_age)):'未收到');
  paint('pkt',s.pkt_age===null?'无':s.pkt_age.toFixed(1)+'s',
        s.pkt_age!==null&&s.pkt_age<6,'累计 '+s.pkt_n+' 个');
  paint('macn',String(s.mac_n),s.mac_n>0,'累计次数');
  paint('up',s.uptime.toFixed(0)+'s',false,'本次监控');

  const vd=document.getElementById('verdict');
  vd.className='verdict '+(s.verdict||'unknown');
  const NM={online:'耳机在线 ✓',offline:'耳机已断开 ✗',unknown:'等待数据…'};
  document.getElementById('vd-main').textContent=NM[s.verdict]||'等待数据…';
  document.getElementById('vd-why').textContent=s.why||'';
  document.getElementById('mode').textContent=s.poll_mode?'主动轮询':'被动';
  document.getElementById('vd-hint').textContent=s.poll_mode
    ?('轮询已发 '+(s.poll_n||0)+' 次') : '判定依据：op=0A 电量';

  let a='<tr><th>偏移</th><th>当前值</th><th>历史值</th><th>是否变过</th><th>最近</th></tr>';
  for(const b of (s.b0a||[])){
    const vary=b.nvar>1;
    const vs=b.vals.map(v=>'0x'+v.toString(16).toUpperCase().padStart(2,'0')).join(' ');
    a+=`<tr><td class="${vary?'hot':'dim'}">byte[${b.i}]</td>
        <td class="${vary?'hot':'dim'}">${hx(b.v)}</td>
        <td class="${vary?'hot':'dim'}" style="font-size:10.5px">${vs}</td>
        <td>${vary?'<span class="pill v">会变</span>':'<span class="pill">恒定</span>'}</td>
        <td class="${vary?'hot':'dim'}">${age(b.age)}</td></tr>`;
  }
  document.getElementById('t0a').innerHTML=a;

  let o='<tr><th>op</th><th>名称</th><th>次数</th><th>载荷种数</th><th>最近</th></tr>';
  for(const[k,v]of Object.entries(s.ops||{})){
    o+=`<tr><td class="hot">${k}</td><td style="font-family:inherit">${v.name}</td>
        <td>${v.n}</td><td>${v.nvar}</td>
        <td class="${v.age<10?'hot':'dim'}">${age(v.age)}</td></tr>`;
  }
  document.getElementById('top').innerHTML=o;

  let p='<tr><th>op</th><th>载荷（前44字符）</th><th>次数</th></tr>';
  for(const[k,v]of Object.entries(s.ops||{})){
    for(const va of v.variants){
      p+=`<tr><td>${k}</td><td style="font-size:10.5px">${va.hex}</td><td>${va.n}</td></tr>`;
    }
  }
  document.getElementById('tpl').innerHTML=p;

  if(s.events.length!==seen){
    seen=s.events.length;
    logEl.innerHTML=s.events.map(e=>
      `<div class="${e.kind}"><span class="t">${e.t}</span>${e.msg}</div>`).join('');
    logEl.scrollTop=logEl.scrollHeight;
  }
  document.getElementById('ft').textContent=
    `监听：${(s.interfaces||[]).join(', ')} · 运行 ${s.uptime.toFixed(0)} 秒 · 报文 ${s.pkt_n} 个`;
 }catch(e){document.getElementById('ft').textContent='与后端失联，重试中…';}
 setTimeout(tick,300);
}
tick();
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
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
    PRIORITY = ("Col03", "Col02", "Col01", "Col04")
    found = {}
    for d in hid.enumerate():
        if d.get("vendor_id") == VID and d.get("product_id") == PID:
            p = d.get("path", b"")
            if isinstance(p, bytes):
                p = p.decode("utf-8", "ignore")
            for c in PRIORITY:
                if c in p and c not in found:
                    found[c] = d["path"]

    targets = [(c, found[c]) for c in PRIORITY if c in found]
    with S.lock:
        S.interfaces = [t[0] for t in targets]

    if not targets:
        print("=" * 60)
        print(" 未找到接收器 %04X:%04X" % (VID, PID))
        print(" 请先插好 USB 接收器，再重新运行。")
        print("=" * 60)
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

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % port
    print("=" * 60)
    print(" 模式：%s" % ("主动轮询" if USE_POLL else "纯被动读取"))
    print(" 观察器已启动：%s" % url)
    print(" 监听接口：%s" % ", ".join(t[0] for t in targets))
    print(" 关闭本窗口即可停止。")
    print("=" * 60)
    threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
