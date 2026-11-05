"""HTTP 服务（标准库，零第三方依赖）。

接口：
    GET /health
    GET /track/<track_id>?name=<歌名>&timeout=<秒>
    GET /queue?limit=<n>

注意：搜索兜底的请求要 60~90 秒，客户端超时必须设得比它长。
"""
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import resolver

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
            return self._send(200, {"ok": True, "service": "soda-proxy"})

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


def serve(host="0.0.0.0", port=18090):
    """启动服务（阻塞）。"""
    srv = ThreadingHTTPServer((host, port), Handler)
    print("[soda-proxy] listening on http://%s:%d" % (host, port))
    srv.serve_forever()


if __name__ == "__main__":
    serve()
