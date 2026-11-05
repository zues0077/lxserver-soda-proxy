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


def clean_name(name):
    if not name:
        return name or ""
    return _BRACKET.sub("", name).strip() or name


def _result(track_id, node, source, extra=None):
    tr, vm, items = queue.streams_of(node)
    out = {
        "ok": bool(items),
        "trackId": str(track_id),
        "name": tr.get("name"),
        "duration": vm.get("video_duration"),
        "source": source,
        "streams": items,
    }
    if extra:
        out.update(extra)
    if not items:
        out["error"] = "记录存在但 video_list 为空（App 尚未补全取链结果）"
    return out


def find_in_queue(track_id):
    """在队列里找该 track 且 video_list 非空；命中返回 playableJson。"""
    if not queue.pull():
        return None
    q = queue.read()
    node = q.get(str(track_id))
    if not node:
        return None
    _, _, items = queue.streams_of(node)
    return node if items else None


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


def resolve(track_id, name=None, timeout=None):
    """对外入口。返回 dict（见 README 的接口契约）。"""
    track_id = str(track_id)
    timeout = timeout or config.RESOLVE_TIMEOUT

    # A. 队列命中
    node = find_in_queue(track_id)
    if node:
        return _result(track_id, node, "queue-cache")

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
