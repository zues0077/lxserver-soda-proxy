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
# ⚠️ 以下坐标按 1080x1920 竖屏实测（平板实测设备为 1200x1848，同样适用）。
# 换分辨率必须重新校准，否则会点到别的控件。
SEARCH_ICON = (1150, 70)       # 右上角放大镜（冷启动落在发现页时）
SEARCH_BOX = (400, 82)         # 搜索输入框
SUGGEST_FIRST = (220, 139)     # 搜索联想第一条
RESULT_X = 220                 # 搜索结果条目点击的 x
# 搜索结果「歌曲」列前 3 条的 y：分别对应「有你可能想搜卡片」和「无卡片」两种布局
RESULT_YS_WITH_CARD = (473, 592, 711)
RESULT_YS_NO_CARD = (353, 472, 591)

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
