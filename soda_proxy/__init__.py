"""汽水取链代理（soda-proxy）。

让官方 App 自己完成签名与取链，我们只读它写进本地数据库的结果：
    track_id -> video_list[].main_url + encrypt_info.spade_a

不涉及 mssdk 签名复刻、不涉及反调试、不抓包。
"""
__version__ = "0.1.0"

from .resolver import resolve, list_queue, clean_name  # noqa: F401
# 不要在这里 import server：`python -m soda_proxy.server` 会因此触发
# "found in sys.modules prior to execution" 警告，并让模块被加载两份。
from . import config, device, queue, resolver  # noqa: F401
