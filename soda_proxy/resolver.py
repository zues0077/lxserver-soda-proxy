"""核心：给定 track_id（可选歌名），拿到整曲直链 + CENC 密钥。

两条路径：
  A. 队列命中 —— cached_playable 里已有该 track_id 且 video_list 非空，毫秒级返回
  B. 搜索兜底 —— adb 搜索歌名 → 点结果条目 → 轮询队列直到目标出现

全程不碰签名、不碰反调试、不抓包：官方 App 自己完成取链。
"""
import re
import time

from . import config, device, queue

# 歌名里的括号/方括号内容要丢掉：「稻香（杰伦）」搜不到，搜「稻香」才有结果
_BRACKET = re.compile(r"[（(【\[].*?[）)】\]]")

# 直链新鲜度：video_model 顶层带 url_expire（秒级时间戳）。
# 过期直链对客户端是 403（加 UA/Referer 都救不回，实测），所以宁可判死也不能把死链交出去。
# 留 2 分钟余量，避免刚给出去就过期。
STALE_MARGIN_SEC = 120


def expire_of(vm):
    try:
        return int((vm or {}).get("url_expire") or 0)
    except (TypeError, ValueError):
        return 0


def is_stale(vm):
    """True = 直链已过期（或即将过期）。没给 url_expire 时不算过期，交给调用方自己试。"""
    exp = expire_of(vm)
    if not exp:
        return False
    return exp - int(time.time()) <= STALE_MARGIN_SEC


def clean_name(name):
    if not name:
        return name or ""
    return _BRACKET.sub("", name).strip() or name


def _result(track_id, node, source, extra=None):
    tr, vm, items = queue.streams_of(node)
    exp = expire_of(vm)
    # 过期直链不能交出去：客户端拿到只会 403，白白把主链路耗掉一轮
    if items and is_stale(vm):
        return {
            "ok": False,
            "trackId": str(track_id),
            "name": tr.get("name"),
            "duration": vm.get("video_duration"),
            "source": source,
            "stale": True,
            "urlExpire": exp,
            "urlExpiresIn": exp - int(time.time()),
            "streams": [],
            "error": "缓存直链已过期（url_expire 已过），需要 App 重新取链",
        }
    out = {
        "ok": bool(items),
        "trackId": str(track_id),
        "name": tr.get("name"),
        "duration": vm.get("video_duration"),
        "source": source,
        "streams": items,
        "urlExpire": exp,
        "urlExpiresIn": (exp - int(time.time())) if exp else None,
        "stale": False,
    }
    if extra:
        out.update(extra)
    if not items:
        out["error"] = "记录存在但 video_list 为空（App 尚未补全取链结果）"
    return out


def find_in_queue(track_id, fresh_only=True):
    """在队列里找该 track 且 video_list 非空；命中返回 playableJson。

    fresh_only=True（默认）时顺带排除「直链已过期」的记录 —— 否则会把死链当命中。
    需要看「有没有但过期了」时传 fresh_only=False。
    """
    if not queue.pull():
        return None
    q = queue.read()
    node = q.get(str(track_id))
    if not node:
        return None
    _, vm, items = queue.streams_of(node)
    if not items:
        return None
    if fresh_only and is_stale(vm):
        return None
    return node


def wait_track(track_id, timeout):
    """轮询等目标 track 出现**且 video_list 写完**。

    只判断 id 存在会拿到半成品 —— App 是先写记录、再异步补 video_model。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.0)
        if not queue.pull():
            continue
        node = queue.read().get(str(track_id))
        if node:
            _, _, items = queue.streams_of(node)
            if items:
                return node
    return None


def resolve(track_id, name=None, timeout=None, fast=False):
    """对外入口。返回 dict（见 README 的接口契约）。

    fast=True 只查播放队列缓存就返回，不做搜索兜底。
    这是给 lxserver 取流主链路用的：队列命中约 1~7 秒，搜索兜底要 60~90 秒，
    主链路等不起，慢路径由后台预热单独跑（见 README「两条路径」）。
    """
    track_id = str(track_id)
    timeout = timeout or config.RESOLVE_TIMEOUT

    # A. 队列命中（只要新鲜直链）
    node = find_in_queue(track_id)
    if node:
        return _result(track_id, node, "queue-cache")

    if fast:
        # 区分「队列没有」和「有记录但直链过期」——后者要明确告诉调用方去预热
        stale_node = find_in_queue(track_id, fresh_only=False)
        if stale_node:
            return _result(track_id, stale_node, "queue-cache")
        return {"ok": False, "trackId": track_id, "stale": False,
                "error": "队列未命中（fast=1，不做搜索兜底）"}

    if not name:
        return {"ok": False, "trackId": track_id,
                "error": "队列未命中且未提供 name（搜索兜底需要歌名）"}

    # B. 搜索兜底（串行，避免与其它请求抢设备）
    with device.device_lock():
        device.ensure_adb_ime()
        tried = []
        for kw in ([clean_name(name)] + ([name] if clean_name(name) != name else [])):
            device.open_search_and_type(kw)
            for idx, y in device.click_results():
                device.tap(config.RESULT_X, y, delay=0.5)
                node = wait_track(track_id, min(config.PICK_WAIT, timeout))
                tried.append({"keyword": kw, "index": idx})
                if node:
                    return _result(track_id, node, "search",
                                   {"picked": {"keyword": kw, "index": idx}})
                device.back()
    # 队列里出现过但没 video_list 的情况，这里再确认一次
    node = find_in_queue(track_id)
    if node:
        return _result(track_id, node, "search")
    return {"ok": False, "trackId": track_id, "tried": tried,
            "error": "搜索结果前几条都没有该 track，或 App 未完成取链"}


def list_queue(limit=50):
    """列出队列里的 track（调试用）。"""
    if not queue.pull():
        return []
    out = []
    for tid, node in list(queue.read().items())[:limit]:
        tr = node.get("track") or {}
        out.append({"trackId": tid, "name": tr.get("name")})
    return out
