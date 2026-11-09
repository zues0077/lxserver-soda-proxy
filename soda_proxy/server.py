"""HTTP 服务（标准库，零第三方依赖）。

接口：
    GET /health
    GET /track/<track_id>?name=<歌名>&timeout=<秒>
    GET /queue?limit=<n>

注意：搜索兜底的请求要 60~90 秒，客户端超时必须设得比它长。
"""
import json
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import captcha, config, device, resolver

# 供外部注入（测试时可替换）
_RESOLVE = resolver.resolve


class Handler(BaseHTTPRequestHandler):
    server_version = "soda-proxy/0.1"

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code, html):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # 默认会往 stderr 打访问日志，保留但简化
        print("[http] " + (fmt % args))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            # 浏览器直接打开 18090 时，原来只有 {"ok":false,"error":"unknown path"}，
            # 看着像服务挂了。给一个落地页：状态 + 账号面板入口 + 接口清单。
            host = self.headers.get("Host") or "127.0.0.1:18090"
            hostname = host.split(":")[0]
            return self._send_html(200, _landing(hostname))

        if path == "/health":
            # root / adb 状态一并暴露：容器重启后 adbd 掉权是高频问题，
            # 面板和 lxserver 都能从这里一眼看出「不是没缓存，是读不到库」
            try:
                rooted = device.is_root()
            except Exception:
                rooted = False
            return self._send(200, {"ok": True, "service": "soda-proxy", "root": rooted})

        if path == "/login/send":
            # 一条龙：打开登录页 -> 填手机号 -> 勾协议 -> 点获取验证码 -> 自动过滑块
            phone = (qs.get("phone") or [""])[0].strip()
            if not re.fullmatch(r"1\d{10}", phone):
                return self._send(400, {"ok": False, "error": "手机号格式不对"})
            with device._DEVICE_LOCK:
                steps = []
                device.shell("am start -n %s" % config.MAIN_ACTIVITY, timeout=40)
                time.sleep(2.0)
                device.tap(*config.ME_TAB, delay=2.5)
                steps.append("打开登录页")
                if not device.ensure_adb_ime():
                    return self._send(500, {"ok": False, "error": "ADBKeyboard 未就绪，无法输入手机号",
                                            "steps": steps})
                device.tap(*config.PHONE_FIELD, delay=1.2)
                device.input_text(phone)
                steps.append("填手机号 " + phone)
                device.tap(*config.AGREE_BOX, delay=1.2)
                steps.append("勾选协议")
                device.tap(*config.SEND_CODE_BTN, delay=4.0)
                steps.append("点获取验证码")
                ok, info = captcha.solve()
                steps.append(("验证通过：" if ok else "验证失败：") + info)
                return self._send(200 if ok else 409, {"ok": ok, "steps": steps})

        if path == "/login/code":
            code = (qs.get("code") or [""])[0].strip()
            if not re.fullmatch(r"\d{4,6}", code):
                return self._send(400, {"ok": False, "error": "验证码格式不对"})
            with device._DEVICE_LOCK:
                device.ensure_adb_ime()
                device.tap(*config.CODE_FIELD, delay=1.2)
                device.input_text(code)
                device.tap(*config.LOGIN_BTN, delay=4.0)
                time.sleep(2.0)
                return self._send(200, {"ok": True, "steps": ["填验证码", "点登录"]})

        if path == "/queue":
            limit = int((qs.get("limit") or ["50"])[0])
            return self._send(200, {"ok": True, "items": resolver.list_queue(limit)})

        if path.startswith("/track/"):
            track_id = path[len("/track/"):].strip()
            if not track_id:
                return self._send(400, {"ok": False, "error": "track_id 不能为空"})
            name = (qs.get("name") or [None])[0]
            fast = (qs.get("fast") or ["0"])[0] in ("1", "true", "yes")
            timeout = qs.get("timeout")
            try:
                timeout = int(timeout[0]) if timeout else None
            except ValueError:
                timeout = None
            try:
                res = _RESOLVE(track_id, name=name, timeout=timeout, fast=fast)
            except Exception as e:              # 任何设备/ADB 异常都转成 502，别让服务挂掉
                return self._send(502, {"ok": False, "trackId": track_id,
                                        "error": "%s: %s" % (type(e).__name__, e)})
            return self._send(200 if res.get("ok") else 404, res)

        return self._send(404, {"ok": False, "error": "unknown path"})


def _landing(host):
    """18090 的落地页：不是 API，是给人看的。"""
    panel = "http://%s:18091/" % host
    try:
        rooted = device.is_root()
    except Exception:
        rooted = None
    badge = ("正常" if rooted else ("可用（adbd 未 root）" if rooted is not None else "未知"))
    color = "#16a34a" if rooted else "#d97706"
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>汽水取链代理</title></head>
<body style="font:15px/1.7 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:760px;margin:40px auto;padding:0 20px;color:#111">
<h1 style="font-size:22px;margin:0 0 4px">汽水取链代理</h1>
<p style="color:#666;margin:0 0 24px">soda-proxy · 给 lxserver 用的 JSON 接口</p>

<div style="border:1px solid #e5e7eb;border-radius:10px;padding:16px 18px;margin-bottom:20px">
  <div style="font-size:13px;color:#666">服务状态</div>
  <div style="margin-top:6px"><span style="display:inline-block;width:8px;height:8px;border-radius:50%%;background:%s;margin-right:8px"></span>%s</div>
</div>

<div style="border:1px solid #e5e7eb;border-radius:10px;padding:16px 18px;margin-bottom:20px">
  <div style="font-size:13px;color:#666;margin-bottom:8px">账号与登录</div>
  <a href="%s" style="display:inline-block;background:#2563eb;color:#fff;text-decoration:none;padding:9px 16px;border-radius:8px;font-size:14px">打开汽水账号面板 →</a>
  <div style="font-size:13px;color:#6b7280;margin-top:10px">%s:18091 · 账号状态、畅听权益、容器保活、远程画面与按键</div>
</div>

<div style="border:1px solid #e5e7eb;border-radius:10px;padding:16px 18px">
  <div style="font-size:13px;color:#666;margin-bottom:10px">接口</div>
  <table style="width:100%%;border-collapse:collapse;font-size:13px">
    <tr><td style="padding:5px 0;color:#374151;white-space:nowrap"><code>GET /health</code></td><td style="color:#6b7280">服务与 adb root 状态</td></tr>
    <tr><td style="padding:5px 0;color:#374151;white-space:nowrap"><code>GET /queue?limit=N</code></td><td style="color:#6b7280">最近播放队列（取链用的缓存）</td></tr>
    <tr><td style="padding:5px 0;color:#374151;white-space:nowrap"><code>GET /track/&lt;id&gt;</code></td><td style="color:#6b7280">取某个 trackId 的直链；<code>?fast=1</code> 只查缓存不驱动 App</td></tr>
  </table>
  <div style="margin-top:12px"><a href="/health" style="color:#2563eb;font-size:13px">测试 /health</a>
  &nbsp;·&nbsp;<a href="/queue?limit=3" style="color:#2563eb;font-size:13px">测试 /queue</a></div>
</div>
</body></html>""" % (color, badge, panel, host)


def _keepalive_loop(interval=120):
    """后台保活：保证 App 进程一直在，且停在后台（按 HOME），几乎不耗 CPU。

    为什么必须：RK3566 上汽水 App **冷启动要 100~120 秒**（类加载+首屏），
    取链请求等不起。常驻后每次只走热启动（几秒）。
    只在「进程不存在」时才拉起，拉起后立刻 HOME 退到后台
    （前台软渲染要 200%+ CPU，后台几乎为 0）。
    """
    time.sleep(5)                     # 让 web 服务先起来
    while True:
        try:
            pid = device.app_pid()
            if pid == 0 and device._DEVICE_LOCK.acquire(blocking=False):
                try:
                    print("[keepalive] 进程不在，开始预热", flush=True)
                    if device.cold_start():
                        # 先暂停再退后台：App 恢复的播放会话会自动放歌，
                        # 后台解码实测把 CPU 从 3.5% 顶到 78%（HDD 上还带 IO）。
                        device.key(127)   # MEDIA_PAUSE
                        device.key(3)     # HOME
                        print("[keepalive] 已预热 pid=%s 并退到后台" % device.app_pid(), flush=True)
                    else:
                        print("[keepalive] 预热失败（App 未到前台）", flush=True)
                finally:
                    device._DEVICE_LOCK.release()
            elif pid:
                print("[keepalive] 在线 pid=%s" % pid, flush=True)
        except Exception as e:
            print("[keepalive] %s: %s" % (type(e).__name__, e), flush=True)
        time.sleep(interval)


def serve(host="0.0.0.0", port=18090):
    """启动服务（阻塞）。"""
    # 启动就抢一次 root：容器重启后 adbd 默认掉回 shell，等第一次请求再修会
    # 把「读不到库」伪装成「队列未命中」，排障时很费时间。
    try:
        device.ensure_root()
    except Exception as e:
        print("[soda-proxy] 启动取 root 失败：%s" % e)
    threading.Thread(target=_keepalive_loop, name="keepalive", daemon=True).start()
    srv = ThreadingHTTPServer((host, port), Handler)
    print("[soda-proxy] listening on http://%s:%d (root=%s)" % (host, port, device.is_root()))
    srv.serve_forever()


if __name__ == "__main__":
    serve()
