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
    """在设备上执行 shell 命令（自动带 -s）。

    A55 这类弱 CPU 上 `input keyevent` 单次可能卡到 20 秒以上（每次都要拉起
    app_process）。这里把超时当空输出处理，不让整条流程因为一个按键挂掉。
    """
    args = config.adb_args() + ["shell", cmd]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return ""


def adb(*args, timeout=30):
    """直接执行 adb 子命令（root / wait-for-device 这类，不走 shell）。"""
    try:
        r = subprocess.run(config.adb_args() + list(args),
                           capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return ""


_IS_ROOT = None
_ROOT_AT = 0.0
# 「不是 root」这个结论**不能永久缓存**：容器重启后 adbd 会掉回 shell，
# 而且 adb root 之后又会变回来。只短期信它，避免一次误判把整轮都判死。
_ROOT_FAIL_TTL = 30.0


def is_root(force=False):
    """adbd 本身是否已经是 root（redroid 容器默认就是，真机 Magisk 不是）。"""
    global _IS_ROOT, _ROOT_AT
    now = time.time()
    if not force and _IS_ROOT is True:
        return True
    if not force and _IS_ROOT is False and now - _ROOT_AT < _ROOT_FAIL_TTL:
        return False
    try:
        _IS_ROOT = shell("id -u", timeout=15).strip() == "0"
    except Exception:
        _IS_ROOT = False
    _ROOT_AT = now
    return _IS_ROOT


def ensure_root():
    """确保 adbd 是 root，不是就 `adb root` 一次。

    为什么必须有：redroid 容器**每次重启后 adbd 都掉回 shell**（uid=2000），
    这时 `ls /data/data/<pkg>/databases` 直接 Permission denied，
    表现却是「队列未命中」——看着像没缓存，实际是读不到库。
    """
    if is_root(force=True):
        return True
    out = adb("root", timeout=30)
    if "cannot run as root" in out:
        # 生产版固件不给 root，只能走 su 分支
        return False
    adb("wait-for-device", timeout=30)
    time.sleep(1.5)
    return is_root(force=True)


def su(cmd, timeout=60):
    """以 root 执行（读 App 私有目录必须）。

    redroid 里未关闭的 adbd 本身就是 root，没有 `su` 这个二进制；
    真机（Magisk）则是 uid=2000 需要走 su。两种情况都要能跑，
    否则换宿主就会稳定报 `su: not found`。

    权限被拒时**自动重取 root 再试一次** —— 覆盖「容器重启后 adbd 掉权」这个高频场景。
    """
    out = ""
    for attempt in (0, 1):
        if is_root():
            out = shell(cmd, timeout=timeout)
        else:
            quoted = "su -c '%s'" % cmd.replace("'", "'\\''")
            out = shell(quoted, timeout=timeout)
            if "not found" in out or "inaccessible" in out:
                # 万一 su 不存在但其实已经有权限，退回直接执行
                out = shell(cmd, timeout=timeout)
        if "Permission denied" not in out:
            return out
        if attempt == 0:
            ensure_root()
    return out


def tap(x, y, delay=1.0):
    shell("input tap %d %d" % (x, y), timeout=20)
    time.sleep(delay)


def key(code, delay=0.6, timeout=20):
    shell("input keyevent %d" % code, timeout=timeout)
    time.sleep(delay)


def input_text(text):
    """通过 ADBKeyboard 输入（含中文）。"""
    shell("am broadcast -a ADB_INPUT_TEXT --es msg '%s'" % text.replace("'", ""), timeout=30)
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
    shell("am force-stop %s" % config.PKG, timeout=25)
    time.sleep(2)
    shell("am start -n %s" % config.MAIN_ACTIVITY, timeout=40)
    # 弱 CPU（RK3566/A55）首屏要 20 秒上下；等不够会点到「×」而不是搜索图标
    time.sleep(20)


def open_search_and_type(name):
    """冷启动 → 搜索页 → 输入歌名 → 点第一条联想进结果页。"""
    cold_start()
    tap(*config.SEARCH_ICON, delay=3.0)
    tap(*config.SEARCH_BOX, delay=2.0)
    # 冷启动后搜索框本来是空的，清空只是兜底。原来 20 次 keyevent 在 A55 上
    # 单次就能卡 20 秒，直接把整条流程拖死 —— 降到 3 次并收紧超时。
    for _ in range(3):
        key(67, 0.05, timeout=8)
    input_text(name)
    tap(*config.SUGGEST_FIRST, delay=5.0)


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
