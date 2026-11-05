"""读取 App 的取链缓存。

核心事实（2026-11-06 实测，见 README「为什么读这个表」）：
    取链结果在 recent_played_<uid>.db 的 cached_playable 表，
    而不是 player_<uid>.db 的 player_info 表（后者只在队列预取时写）。

    cached_playable(id, queueId, playableType, playableJson)
        id           = track_id
        playableJson → track.player_info.video_model（JSON 字符串，需二次 parse）
                       → video_list[].main_url + encrypt_info.spade_a
"""
import json
import os
import shutil
import sqlite3
import tempfile

from . import config, device

_LOCAL_DIR = None


def _workdir():
    global _LOCAL_DIR
    if _LOCAL_DIR is None:
        _LOCAL_DIR = config.WORK_DIR or tempfile.mkdtemp(prefix="soda_proxy_")
        os.makedirs(_LOCAL_DIR, exist_ok=True)
    return _LOCAL_DIR


def _db_paths():
    """列出设备上所有 recent_played*.db，按名字长度降序（uid 版优先，避开 recent_played_0.db）。"""
    out = device.su("ls %s" % config.DB_DIR, timeout=30)
    names = []
    for line in out.splitlines():
        name = line.strip().split("/")[-1]
        if name.startswith(config.QUEUE_DB_PREFIX) and name.endswith(".db"):
            names.append(name)
    names.sort(key=len, reverse=True)
    return names


def pull(timeout=60):
    """把队列库（含 WAL）拉到本地，返回本地 db 路径；失败返回 None。"""
    names = _db_paths()
    if not names:
        return None
    remote = "%s/%s" % (config.DB_DIR, names[0])
    device.su(
        "cp {p} /sdcard/_soda_q.db; cp {p}-wal /sdcard/_soda_q.db-wal 2>/dev/null; "
        "chmod 644 /sdcard/_soda_q.db /sdcard/_soda_q.db-wal 2>/dev/null".format(p=remote),
        timeout=timeout)

    local_db = os.path.join(_workdir(), "q.db")
    local_wal = local_db + "-wal"
    for remote_file, local in (("/sdcard/_soda_q.db", local_db),
                               ("/sdcard/_soda_q.db-wal", local_wal)):
        if os.path.exists(local):
            os.remove(local)
        args = config.adb_args() + ["pull", remote_file, local]
        import subprocess
        subprocess.run(args, capture_output=True, text=True, timeout=90)
    return local_db if os.path.exists(local_db) else None


def read(db_path=None):
    """读全量队列，返回 {track_id: playable_json_dict}。"""
    db = db_path or os.path.join(_workdir(), "q.db")
    if not os.path.exists(db):
        return {}
    out = {}
    con = sqlite3.connect(db)
    try:
        cur = con.cursor()
        cur.execute("SELECT id, playableJson FROM cached_playable")
        for tid, pj in cur.fetchall():
            try:
                out[str(tid)] = json.loads(pj)
            except Exception:
                continue
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    return out


def extract(node):
    """从 playableJson 里取 (track, video_model)。video_model 是 JSON 字符串，需二次 parse。"""
    tr = node.get("track") or {}
    vm = (tr.get("player_info") or {}).get("video_model") or node.get("video_model")
    if isinstance(vm, str):
        try:
            vm = json.loads(vm)
        except Exception:
            vm = None
    return tr, (vm or {})


def streams_of(node):
    """把 video_model 转成统一结构（按 bitrate 降序）。"""
    tr, vm = extract(node)
    items = []
    for v in (vm.get("video_list") or []):
        ei = v.get("encrypt_info") or {}
        meta = v.get("video_meta") or {}
        url = v.get("main_url") or v.get("backup_url") or ""
        if not str(url).startswith("http"):
            continue
        items.append({
            "quality": meta.get("quality"),
            "bitrate": meta.get("bitrate"),
            "size": meta.get("size"),
            "main_url": url,
            "spade_a": ei.get("spade_a"),
            "encrypt": ei.get("encrypt"),
            "kid": ei.get("kid"),
        })
    items.sort(key=lambda x: -(x.get("bitrate") or 0))
    return tr, vm, items


def cleanup():
    """删除本地临时库文件（含 WAL/SHM）。"""
    if _LOCAL_DIR and os.path.isdir(_LOCAL_DIR):
        shutil.rmtree(_LOCAL_DIR, ignore_errors=True)
