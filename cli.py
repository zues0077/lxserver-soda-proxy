#!/usr/bin/env python3
"""命令行入口。

    python cli.py --list                      # 列出 App 队列里已有的歌
    python cli.py <track_id>                  # 取链（队列命中时秒级）
    python cli.py <track_id> --name 晴天       # 队列未命中时走搜索兜底
    python cli.py <track_id> --name 晴天 --json out.json
"""
import argparse
import json
import sys

from soda_proxy import resolver
from soda_proxy import config


def main():
    ap = argparse.ArgumentParser(description="汽水取链代理")
    ap.add_argument("track_id", nargs="?", help="汽水 track_id（数字）")
    ap.add_argument("--name", help="歌名，队列未命中时用于搜索兜底")
    ap.add_argument("--timeout", type=int, default=None, help="搜索兜底超时秒数")
    ap.add_argument("--json", help="把完整结果写到文件")
    ap.add_argument("--list", action="store_true", help="列出队列已有的歌")
    ap.add_argument("--limit", type=int, default=50)
    a = ap.parse_args()

    if a.list:
        items = resolver.list_queue(a.limit)
        print("队列内 %d 条：" % len(items))
        for it in items:
            print("  %-20s %s" % (it["trackId"], it.get("name")))
        return 0

    if not a.track_id:
        ap.print_help()
        return 2

    res = resolver.resolve(a.track_id, name=a.name, timeout=a.timeout)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)

    if res.get("ok"):
        print("OK  [%s] %s | %s | %ss | %d streams" % (
            res["source"], res["trackId"], res.get("name"),
            res.get("duration"), len(res["streams"])))
        for s in res["streams"][:8]:
            print("    %-9s br=%-8s size=%-9s spade=%d" % (
                s.get("quality"), s.get("bitrate"), s.get("size"),
                len(str(s.get("spade_a") or ""))))
        return 0

    print("FAIL", res.get("error"))
    if res.get("tried"):
        print("  已尝试:", res["tried"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
