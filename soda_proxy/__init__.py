"""汽水取链代理（soda-proxy）。

让官方 App 自己完成签名与取链，我们只读它写进本地数据库的结果：
    track_id -> video_list[].main_url + encrypt_info.spade_a

不涉及 mssdk 签名复刻、不涉及反调试、不抓包。
"""
__version__ = "0.1.0"

from .resolver import resolve, list_queue, clean_name  # noqa: F401
from . import config, device, queue, resolver, server  # noqa: F401
