#!/usr/bin/env python3
"""汽水容器的网页操作台（只依赖标准库 + adb）。

为什么需要它：容器的 5555 是 **adb 协议端口**，浏览器打不开；scrcpy 要装客户端、
且进程容易被回收。这个页面把「截图 + 点击 + 输入」包成 HTTP，浏览器打开就能登录汽水。

用法：
    python3 webui.py --serial 127.0.0.1:5555 --port 18091

接口：
    GET /                 操作页面
    GET /shot             当前屏幕 PNG
    GET /tap?x=&y=        点击（设备像素坐标）
    GET /text?s=          输入文本（数字/英文可靠；中文需 ADBKeyboard）
    GET /key?k=4          按键（4=返回 3=主页 66=回车 67=删除）
    GET /swipe?x1=&y1=&x2=&y2=&ms=
    GET /health           {"ok":true,...}

注意：**没有鉴权**，只应监听内网。它等于把一台手机的完全控制权挂到 HTTP 上。
"""
import argparse
import io
import json
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {'serial': '127.0.0.1:5555', 'adb': 'adb', 'last_shot': None, 'last_shot_at': 0}
_LOCK = threading.Lock()


def adb(*args, timeout=30):
    cmd = [STATE['adb']]
    if STATE['serial']:
        cmd += ['-s', STATE['serial']]
    cmd += list(args)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return r.returncode, (r.stdout or b''), (r.stderr or b'')


def ensure_device():
    """确保 adb server 起来、设备已连接。返回 (ok, message)。"""
    adb('start-server', timeout=20)
    code, out, err = adb('get-state', timeout=15)
    if out.decode('utf-8', 'replace').strip() == 'device':
        return True, 'device'
    adb('connect', STATE['serial'], timeout=15)
    time.sleep(1.5)
    code, out, err = adb('get-state', timeout=15)
    state = out.decode('utf-8', 'replace').strip()
    return (state == 'device'), (state or err.decode('utf-8', 'replace').strip())


def screenshot():
    code, out, err = adb('exec-out', 'screencap', '-p', timeout=40)
    if code == 0 and out[:4] == b'\x89PNG':
        STATE['last_shot'] = out
        STATE['last_shot_at'] = time.time()
        return out
    return None


PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>汽水容器 操作台</title>
<style>
:root{color-scheme:dark}
body{margin:0;background:#111;color:#e5e7eb;font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;display:flex;height:100vh;overflow:hidden}
#left{flex:1;display:flex;align-items:center;justify-content:center;padding:12px;min-width:0}
#wrap{position:relative;max-height:100%;max-width:100%}
#scr{max-height:calc(100vh - 24px);max-width:100%;display:block;cursor:crosshair;border-radius:8px}
#right{width:320px;flex:0 0 320px;border-left:1px solid #262626;padding:14px;display:flex;flex-direction:column;gap:10px;overflow:auto}
h1{font-size:15px;margin:0 0 4px}
.muted{color:#8b8b8b;font-size:12px}
.row{display:flex;gap:8px}
button{background:#262626;color:#e5e7eb;border:1px solid #3a3a3a;border-radius:6px;padding:8px 10px;font-size:13px;cursor:pointer}
button:hover{background:#333}
button.primary{background:#16a34a;border-color:#16a34a;color:#fff}
input{flex:1;background:#1c1c1c;color:#e5e7eb;border:1px solid #3a3a3a;border-radius:6px;padding:8px;font-size:13px;min-width:0}
#status{font-size:12px;color:#9ca3af;white-space:pre-wrap;word-break:break-all}
</style></head>
<body>
<div id="left"><div id="wrap"><img id="scr" alt="屏幕"></div></div>
<div id="right">
  <h1>汽水容器 操作台</h1>
  <div class="muted">点画面 = 点击；下面可输入手机号/验证码</div>
  <div class="row"><input id="txt" placeholder="数字/英文，如手机号或验证码"><button onclick="sendText()">输入</button></div>
  <div class="row">
    <button onclick="key(4)">返回</button>
    <button onclick="key(3)">主页</button>
    <button onclick="key(67)">删除</button>
    <button onclick="key(66)">回车</button>
  </div>
  <div class="row"><button class="primary" onclick="refresh()">刷新画面</button><button onclick="reconnect()">重连设备</button></div>
  <div id="status">加载中…</div>
</div>
<script>
const img=document.getElementById('scr'), st=document.getElementById('status');
let timer=null;
function setStatus(t){st.textContent=t}
function refresh(){
  const t=Date.now();
  const i=new Image();
  i.onload=()=>{img.src=i.src; setStatus('画面 '+new Date().toLocaleTimeString());};
  i.onerror=()=>setStatus('截图失败（设备可能掉线）');
  i.src='/shot?t='+t;
}
function loop(){ clearInterval(timer); timer=setInterval(refresh, 1500); }
function sendText(){
  const v=document.getElementById('txt').value; if(!v) return;
  fetch('/text?s='+encodeURIComponent(v)).then(r=>r.text()).then(t=>setStatus(t));
  document.getElementById('txt').value='';
}
function key(k){ fetch('/key?k='+k).then(r=>r.text()).then(t=>setStatus(t)); setTimeout(refresh,600); }
function reconnect(){ fetch('/reconnect').then(r=>r.text()).then(t=>setStatus(t)); }
img.addEventListener('click', e=>{
  const r=img.getBoundingClientRect();
  const x=Math.round((e.clientX-r.left)*img.naturalWidth/r.width);
  const y=Math.round((e.clientY-r.top)*img.naturalHeight/r.height);
  setStatus('点击 '+x+','+y);
  fetch('/tap?x='+x+'&y='+y).then(r=>r.text()).then(()=>setTimeout(refresh,500));
});
loop(); refresh();
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = 'soda-webui/0.1'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype='text/plain; charset=utf-8'):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path

        if p == '/':
            return self._send(200, PAGE, 'text/html; charset=utf-8')

        if p == '/health':
            ok, msg = ensure_device()
            return self._send(200, json.dumps({'ok': ok, 'device': msg, 'serial': STATE['serial']}),
                              'application/json; charset=utf-8')

        if p == '/reconnect':
            adb('kill-server', timeout=15)
            ok, msg = ensure_device()
            return self._send(200, '重连：%s（%s）' % ('成功' if ok else '失败', msg))

        if p == '/shot':
            png = None
            with _LOCK:
                png = screenshot()
            if png:
                return self._send(200, png, 'image/png')
            if STATE['last_shot']:
                return self._send(200, STATE['last_shot'], 'image/png')
            return self._send(503, 'no screenshot')

        if p == '/tap':
            try:
                x = int(q.get('x', ['0'])[0]); y = int(q.get('y', ['0'])[0])
            except ValueError:
                return self._send(400, 'bad coords')
            adb('shell', 'input', 'tap', str(x), str(y), timeout=20)
            return self._send(200, 'tap %d,%d' % (x, y))

        if p == '/text':
            s = q.get('s', [''])[0]
            if not s:
                return self._send(400, 'empty')
            # input text 不支持空格与部分符号，空格要转成 %s
            safe = s.replace(' ', '%s').replace('&', '\\&')
            adb('shell', 'input', 'text', safe, timeout=20)
            return self._send(200, '已输入 %d 个字符' % len(s))

        if p == '/key':
            k = q.get('k', ['4'])[0]
            if not k.isdigit():
                return self._send(400, 'bad key')
            adb('shell', 'input', 'keyevent', k, timeout=20)
            return self._send(200, 'key %s' % k)

        if p == '/swipe':
            try:
                x1 = int(q.get('x1', ['0'])[0]); y1 = int(q.get('y1', ['0'])[0])
                x2 = int(q.get('x2', ['0'])[0]); y2 = int(q.get('y2', ['0'])[0])
                ms = int(q.get('ms', ['300'])[0])
            except ValueError:
                return self._send(400, 'bad coords')
            adb('shell', 'input', 'swipe', str(x1), str(y1), str(x2), str(y2), str(ms), timeout=25)
            return self._send(200, 'swipe')

        return self._send(404, 'not found')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--serial', default='127.0.0.1:5555')
    ap.add_argument('--port', type=int, default=18091)
    ap.add_argument('--host', default='0.0.0.0')
    ap.add_argument('--adb', default='adb')
    a = ap.parse_args()

    STATE['serial'] = a.serial
    STATE['adb'] = a.adb
    ok, msg = ensure_device()
    print('[webui] device: %s (%s)' % ('OK' if ok else 'FAIL', msg), flush=True)

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print('[webui] http://%s:%d' % (a.host, a.port), flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
