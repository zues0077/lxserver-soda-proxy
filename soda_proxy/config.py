"""运行时配置：全部可用环境变量覆盖，便于在不同设备/容器上部署。"""
import os

# ---- adb ----
ADB_BIN = os.environ.get("SODA_ADB", "adb")
# 空字符串 = 用默认设备；多设备时必须指定，如 "192.168.1.177:5555"
ADB_SERIAL = os.environ.get("SODA_SERIAL", "")

# ---- 目标 App ----
PKG = os.environ.get("SODA_PKG", "com.luna.music")
MAIN_ACTIVITY = PKG + "/com.luna.biz.main.main.MainActivity"
DB_DIR = "/data/data/%s/databases" % PKG
QUEUE_DB_PREFIX = "recent_played"

# ---- 超时（秒）----
# 单次取链总预算：队列命中几乎瞬间；走搜索兜底实测 60~70 秒
RESOLVE_TIMEOUT = int(os.environ.get("SODA_TIMEOUT", "90"))
# 点一条搜索结果后，等队列写入的窗口
PICK_WAIT = int(os.environ.get("SODA_PICK_WAIT", "6"))

# ---- 工作目录（拉下来的数据库临时文件）----
WORK_DIR = os.environ.get("SODA_WORK_DIR", None)

# ---- UI 坐标 ----
# ⚠️ 下面这组坐标是「dump 不出层级时的兜底」，按 1200x1848 竖屏实测标定。
# 正常路径走 ui.py 的节点定位（按 text / resource-id 找，换分辨率不失效）；
# 只有 uiautomator dump 失败（页面动画导致 waitForIdle 超时）才会用到这些数字，
# 而且会经 ui.scale_point() 按当前分辨率等比缩放。
BASE_SIZE = (1200, 1848)
SEARCH_ICON = (1150, 70)       # 右上角放大镜（冷启动落在发现页时）
SEARCH_BOX = (400, 82)         # 搜索输入框
SUGGEST_FIRST = (220, 139)     # 搜索联想第一条
RESULT_X = (220, 0)            # 搜索结果条目点击的 x（只取 x 分量）
# 搜索结果「歌曲」列前 3 条的 y（只取 y 分量）
RESULT_Y1 = (0, 473)
RESULT_Y2 = (0, 592)
RESULT_Y3 = (0, 711)
RESULT_YS_WITH_CARD = (473, 592, 711)
RESULT_YS_NO_CARD = (353, 472, 591)

# 分分辨率坐标表（ui.profile_point 优先查这里，查不到才对 BASE_SIZE 等比缩放）。
# ⚠️ 等比缩放**不可靠**：1200x1848(dpi240) 是类平板布局、720x1280(dpi320) 是手机布局，
# 搜索图标在两者里的相对位置就不一样（0.958,0.038 vs 0.906,0.073），所以小分辨率必须单独标定。
PROFILES = {
    # 720x1280 / dpi320（标准手机布局）实测标定：
    #   搜索图标 = 右上角放大镜 (652,94)；进搜索页后输入框自动获得焦点，直接广播输入即可；
    #   联想第一条 y≈187；搜索结果第一首歌 y≈641（往下每条 +144）。
    "720x1280": {
        "SEARCH_ICON": (652, 94),
        "SEARCH_BOX": (300, 110),
        "SUGGEST_FIRST": (350, 187),
        "RESULT_X": (360, 0),
        "RESULT_Y1": (0, 641),
        "RESULT_Y2": (0, 785),
        "RESULT_Y3": (0, 929),
    },
}

# ---- 输入法 ----
# adb shell input text 会丢弃中文，必须用 ADBKeyboard 才能输入歌名
ADB_IME = os.environ.get("SODA_ADB_IME", "com.android.adbkeyboard/.AdbIME")

# ---- 打包尺寸取舍 ----
# HTTP 层默认只返回这些档位（None = 全部返回）
DEFAULT_QUALITIES = None


def adb_args():
    """返回调用 adb 时的公共参数（含 -s 指定设备）。"""
    args = [ADB_BIN]
    if ADB_SERIAL:
        args += ["-s", ADB_SERIAL]
    return args
