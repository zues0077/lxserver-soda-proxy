"""adb 操作封装：把"让官方 App 播放指定歌曲"这件事做成可复用函数。

设计原则：
- 所有 adb 调用都走同一个串行锁 —— 代理是单设备，并发点歌会互相干扰。
- UI 坐标集中在 config.py，换设备只改那一处。
"""
import subprocess
import threading
import time

from . import config

# 全局串行锁：同一时刻只允许一个 adb 交互流程（可能持续 60~70 秒）
_DEVICE_LOCK = threading.Lock()


def shell(cmd, timeout=60):
    """在设备上执行 shell 命令（自动带 -s）。"""
    args = config.adb_args() + ["shell", cmd]
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "") + (r.stderr or "")


def su(cmd, timeout=60):
    """以 root 执行（读 App 私有目录必须）。"""
    return shell("su -c '%s'" % cmd.replace("'", "'\\''"), timeout=timeout)


def tap(x, y, delay=1.0):
    shell("input tap %d %d" % (x, y), timeout=20)
    time.sleep(delay)


def key(code, delay=0.6):
    shell("input keyevent %d" % code, timeout=20)
    time.sleep(delay)


def input_text(text):
    """通过 ADBKeyboard 输入（含中文）。"""
    shell("am broadcast -a ADB_INPUT_TEXT --es msg '%s'" % text.replace("'", ""), timeout=20)
    time.sleep(1.2)


def app_pid():
    """返回 App 主进程 pid，未运行返回 0。"""
    out = shell("ps -A | grep %s" % config.PKG, timeout=20)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) > 1 and config.PKG in line:
            try:
                return int(parts[1])
            except ValueError:
                continue
    return 0


def ensure_adb_ime():
    """确保 ADBKeyboard 是当前输入法（切 IME 必须 su，Android 9 直接 ime set 会抛异常）。"""
    out = shell("settings get secure default_input_method", timeout=15).strip()
    if out == config.ADB_IME:
        return True
    su("ime enable %s" % config.ADB_IME, timeout=20)
    su("ime set %s" % config.ADB_IME, timeout=20)
    return True


def cold_start():
    """冷启动到发现页。

    必须冷启动：App 停在结果页时右上角是「×」而不是搜索图标，
    点 (1150,70) 只会清空、不会进搜索页。
    """
    shell("am force-stop %s" % config.PKG, timeout=20)
    time.sleep(1.5)
    shell("am start -n %s" % config.MAIN_ACTIVITY, timeout=30)
    time.sleep(9)


def open_search_and_type(name):
    """冷启动 → 搜索页 → 输入歌名 → 点第一条联想进结果页。"""
    cold_start()
    tap(*config.SEARCH_ICON, delay=2.5)
    tap(*config.SEARCH_BOX, delay=1.5)
    for _ in range(20):
        key(67, 0.03)          # DEL 清空历史内容
    input_text(name)
    tap(*config.SUGGEST_FIRST, delay=4.0)


def click_results(max_per_layout=3):
    """依次点搜索结果条目的生成器：先试「有卡片」布局，再试「无卡片」布局。"""
    for ys in (config.RESULT_YS_WITH_CARD, config.RESULT_YS_NO_CARD):
        for idx, y in enumerate(ys[:max_per_layout]):
            yield idx, y


def back(delay=1.2):
    key(4, delay)


class device_lock(object):
    """用 with device_lock(): 包住整段 UI 交互。"""

    def __enter__(self):
        _DEVICE_LOCK.acquire()
        return self

    def __exit__(self, *exc):
        _DEVICE_LOCK.release()
        return False
