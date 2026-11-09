"""HTTP 服务（标准库，零第三方依赖）。

接口：
    GET /health
    GET /track/<track_id>?name=<歌名>&timeout=<秒>
    GET /queue?limit=<n>

注意：搜索兜底的请求要 60~90 秒，客户端超时必须设得比它长。
"""
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import device, resolver

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

    def log_message(self, fmt, *args):
        # 默认会往 stderr 打访问日志，保留但简化
        print("[http] " + (fmt % args))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/health":
            # root / adb 状态一并暴露：容器重启后 adbd 掉权是高频问题，
            # 面板和 lxserver 都能从这里一眼看出「不是没缓存，是读不到库」
            try:
                rooted = device.is_root()
            except Exception:
                rooted = False
            return self._send(200, {"ok": True, "service": "soda-proxy", "root": rooted})

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
            if device.app_pid() == 0 and device._DEVICE_LOCK.acquire(blocking=False):
                try:
                    if device.cold_start():
                        device.key(3)     # HOME：退到后台
                        print("[keepalive] App 已预热并退到后台", flush=True)
                finally:
                    device._DEVICE_LOCK.release()
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
