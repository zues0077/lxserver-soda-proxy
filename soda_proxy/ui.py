"""UI 视图树读取与节点定位（替代硬编码点击坐标）。

## 为什么不用 uiautomator

实测这个 redroid 镜像里 `uiautomator dump` **对任何 App 都失败**（连系统设置页也报
`ERROR: null root node returned by UiTestAutomationBridge`），无障碍/UiAutomation 是坏的。
所以改读 `dumpsys activity top`——它同样给出完整 View 树，而且**更快**（几百毫秒）、
还带 clickable 标志。

## 树的格式（一行一个 View）

```
android.widget.TextView{1c84975 VFED..C.. ......I. 0,0-0,0 #7f070014 app:id/search_widget_text}
```

- `{` 后面是 8~9 位 flag，其中 **`C` = clickable、`L` = long-clickable**
- 中间的 `l,t-r,b` 是 bounds，**注意是相对父容器的**（dumpsys 打的是 mLeft/mTop，
  不含 translation/scroll 偏移）。顶部标题栏那种父容器在 (0,0) 的，local≈absolute；
  但底栏这类用 translationY 移上去的，local 全是 0,x-…，**不能当屏幕坐标用来点**。
  底栏/悬浮元素一律用截图量坐标，不要信 dump。
- 末尾 `#7f... app:id/xxx` 是资源 id（汽水的 id 基本被混淆成 `ho1`/`hky`，只能按类名+位置找）

## 定位策略

汽水没有语义化的 search id，所以按「类名 + 位置」启发式找：
- 搜索图标：右上角区域内、可点、面积不大的那个 View
- 搜索框：class 含 EditText
- 结果条目：列表区域内高度像一行、宽度接近满屏的可点 View，按 y 排序取前几条

全部失败才退回 `config.PROFILES` 里的坐标（按分辨率查表，查不到就等比缩放）。
"""
import re

from . import config, device

# 一行一个 View；flags 段里可能带空格，用非贪婪匹配到 bounds 之前
_LINE = re.compile(
    r"^(?P<indent>\s*)(?P<cls>[A-Za-z_$][\w.$]*)\{(?P<ref>[0-9a-f]+)\s+(?P<mid>.+?)\s+"
    r"(?P<x1>-?\d+),(?P<y1>-?\d+)-(?P<x2>-?\d+),(?P<y2>-?\d+)(?P<tail>.*)$")
_RID = re.compile(r"app:id/([\w]+)")


def _pick_section(text):
    """只保留目标 Activity 那一段 View Hierarchy。

    `dumpsys activity top` 会把**多个窗口**都 dump 出来（实测桌面 QuickstepLauncher
    在前、MainActivity 在后）。不筛的话会解析到桌面的控件，
    表现是「找到了搜索图标，但点上去毫无反应」——这个坑踩过一次。
    """
    lines = (text or "").splitlines()
    idxs = [i for i, l in enumerate(lines) if "View Hierarchy:" in l]
    if not idxs:
        return text or ""
    short = config.MAIN_ACTIVITY.split("/")[-1].split(".")[-1]   # MainActivity
    picked = None
    for k, i in enumerate(idxs):
        end = idxs[k + 1] if k + 1 < len(idxs) else len(lines)
        sec = lines[i:end]
        head = sec[1] if len(sec) > 1 else ""
        if short and short in head:
            picked = sec
    if picked is None:
        picked = lines[idxs[-1]:]      # 没匹配上就取最后一段（最顶层窗口）
    # 最后一段还是桌面（Launcher）→ 说明这次 dump 没覆盖到目标 App。
    # 必须当成「没拿到」返回空，否则调用方会把桌面的搜索框当成 App 的搜索图标，
    # 点上去毫无反应还查不出原因（这条踩过）。
    head = picked[1] if len(picked) > 1 else ""
    if "Launcher" in head or "DecorView@" not in head:
        return ""
    return "\n".join(picked)


def raw_dump(timeout=25):
    """拿当前顶层 Activity 的 View 树文本（已过滤掉其它窗口）。"""
    return _pick_section(device.shell("dumpsys activity top", timeout=timeout))


def parse(text):
    """解析成节点列表，保持层级顺序（父在前、兄弟按添加顺序）。"""
    nodes = []
    for line in (text or "").splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        x1, y1, x2, y2 = (int(m.group(k)) for k in ("x1", "y1", "x2", "y2"))
        if x2 <= x1 or y2 <= y1:
            continue
        mid = m.group("mid")
        rid_m = _RID.search(m.group("tail") or "")
        nodes.append({
            "cls": m.group("cls"),
            "rid": rid_m.group(1) if rid_m else "",
            "clickable": "C" in mid,
            "bounds": (x1, y1, x2, y2),
            "center": ((x1 + x2) // 2, (y1 + y2) // 2),
            "w": x2 - x1,
            "h": y2 - y1,
            "area": (x2 - x1) * (y2 - y1),
            "depth": len(m.group("indent")),
        })
    return nodes


def dump_nodes(timeout=25):
    return parse(raw_dump(timeout))


def screen_size():
    """当前逻辑分辨率。"""
    out = device.shell("wm size", timeout=15)
    m = re.search(r"Override size:\s*(\d+)x(\d+)", out) or re.search(r"Physical size:\s*(\d+)x(\d+)", out)
    if m:
        return int(m.group(1)), int(m.group(2))
    return config.BASE_SIZE


# ---------------------------------------------------------------- 查找

def by_class(nodes, *names):
    for n in nodes:
        for name in names:
            if name in n["cls"]:
                return n
    return None


def search_entry(nodes, size):
    """右上角的搜索图标。

    实测踩过的坑：右上角有多个可点节点（金币/头像/放大镜），按「面积最小」挑会挑到
    图标字体那个小 view（点上去毫无反应）。真正的按钮是**贴着右边缘**的那个，
    所以按「右边缘越靠右越优先，同分取面积大」排序。
    """
    w, h = size
    best = None
    best_key = None
    for n in nodes:
        if not n["clickable"]:
            continue
        x1, y1, x2, y2 = n["bounds"]
        if y2 > h * 0.18:            # 只在上部
            continue
        if x1 < w * 0.60:            # 只在右侧
            continue
        if n["w"] > w * 0.35 or n["h"] < h * 0.035 or n["h"] > h * 0.12:
            continue
        # 桌面 launcher 的搜索控件也长在右上角，必须排掉
        if "Launcher" in n["cls"] or "DoubleShadow" in n["cls"] or "launcher" in n["rid"]:
            continue
        key = (x2, n["area"])        # 越贴右边越好；同位置取大的
        if best_key is None or key > best_key:
            best_key = key
            best = n
    return best


def search_box(nodes):
    """搜索页的输入框。"""
    return by_class(nodes, "EditText", "AutoCompleteTextView", "SearchView")


def suggestion_rows(nodes, size, limit=3):
    """搜索联想列表的条目（输入完歌名后出现）。

    实测这类条目是 `RecommendPreferenceView`（汽水自己的类名），
    拿不到时退回「列表区域内、高度像一行、宽度够宽」的可点节点。
    """
    w, h = size
    out = []
    for n in nodes:
        if not n["clickable"]:
            continue
        x1, y1, x2, y2 = n["bounds"]
        if "RecommendPreference" in n["cls"]:
            if y1 < h * 0.08 or y2 > h * 0.75:
                continue
            out.append(n)
    if out:
        out.sort(key=lambda n: n["bounds"][1])
        return out[:limit]

    for n in nodes:
        if not n["clickable"]:
            continue
        x1, y1, x2, y2 = n["bounds"]
        if y1 < h * 0.10 or y2 > h * 0.70:
            continue
        if n["h"] < h * 0.05 or n["h"] > h * 0.12:
            continue
        if n["w"] < w * 0.6:
            continue
        out.append(n)
    out.sort(key=lambda n: n["bounds"][1])
    return out[:limit]


def result_rows(nodes, size, limit=4):
    """搜索结果里的**歌曲行**。

    实测：汽水的歌曲条目类是 `TrackView`（高约 144），而「你可能想搜」那张卡片是
    `ArtistView`——两者都是「整宽可点」，只按尺寸筛会点到艺人卡片（点了不播放，
    白白浪费一次 PICK_WAIT）。所以优先按类名锚定。
    """
    rows = [n for n in nodes if n["clickable"] and "TrackView" in n["cls"]]
    if rows:
        rows.sort(key=lambda n: n["bounds"][1])
        return rows[:limit]

    # 兜底：整宽、高度像一行、且不在顶部标题区
    w, h = size
    rows = []
    for n in nodes:
        if not n["clickable"]:
            continue
        x1, y1, x2, y2 = n["bounds"]
        if y1 < h * 0.22 or y2 > h * 0.92:
            continue
        if n["h"] < 60 or n["h"] > h * 0.15:
            continue
        if n["w"] < w * 0.75:
            continue
        rows.append(n)
    rows.sort(key=lambda n: n["bounds"][1])
    # 去掉纵向重叠
    out = []
    for n in rows:
        if out and n["bounds"][1] < out[-1]["bounds"][3] - 4:
            continue
        out.append(n)
    return out[:limit]


# ---------------------------------------------------------------- 兜底坐标

def profile_point(name):
    """按当前分辨率查坐标表；查不到就按 BASE_SIZE 等比缩放。"""
    w, h = screen_size()
    prof = config.PROFILES.get("%dx%d" % (w, h)) if getattr(config, "PROFILES", None) else None
    if prof and name in prof:
        return prof[name]
    pt = getattr(config, name)
    bw, bh = config.BASE_SIZE
    return int(round(pt[0] * w / float(bw))), int(round(pt[1] * h / float(bh)))


def ensure_quiet_animations():
    """关掉系统动画。dumpsys 不需要它，但能减少渲染开销、让页面更快稳定。"""
    for k in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
        device.shell("settings put global %s 0" % k, timeout=15)


# ---------------------------------------------------------------- 高层流程

def tap_point(pt, delay=1.0):
    device.tap(pt[0], pt[1], delay=delay)
    return True


def open_search_and_type(name, verbose=True):
    """冷启动 → 搜索页 → 输入歌名 → 进结果页。返回是否走完（False = 冷启动就没起来）。"""
    if not device.warm_or_cold_start():
        if verbose:
            print("[ui] 冷/热启动超时，App 没到前台，放弃本次")
        return False
    device.ensure_adb_ime()
    size = screen_size()

    # 热启动时 App 可能停在搜索页/结果页，那里右上角是「×」不是放大镜。
    # 直接点底部的「发现」tab 回首页，比按 BACK 稳（BACK 在首页会直接退出 App）。
    tap_point(profile_point("HOME_TAB"), delay=2.5)

    # 1) 点搜索图标
    nodes = dump_nodes()
    icon = search_entry(nodes, size)
    if icon:
        tap_point(icon["center"], delay=3.0)
        if verbose:
            print("[ui] 搜索图标：%s %s" % (icon["cls"].split(".")[-1], icon["bounds"]))
    else:
        tap_point(profile_point("SEARCH_ICON"), delay=3.0)
        if verbose:
            print("[ui] 搜索图标：用兜底坐标")

    # 2) 点输入框
    nodes = dump_nodes()
    box = search_box(nodes)
    if box:
        tap_point(box["center"], delay=2.0)
        if verbose:
            print("[ui] 搜索框：%s %s" % (box["cls"].split(".")[-1], box["bounds"]))
    else:
        tap_point(profile_point("SEARCH_BOX"), delay=2.0)
        if verbose:
            print("[ui] 搜索框：用兜底坐标")

    # 3) 清空 + 输入（中文必须走 ADBKeyboard）
    for _ in range(3):
        device.key(67, 0.05, timeout=8)
    device.input_text(name)

    # 4) 点联想第一条进结果页
    nodes = dump_nodes()
    sug = suggestion_rows(nodes, size)
    if sug:
        tap_point(sug[0]["center"], delay=5.0)
        if verbose:
            print("[ui] 联想第一条：%s %s" % (sug[0]["cls"].split(".")[-1], sug[0]["bounds"]))
    else:
        tap_point(profile_point("SUGGEST_FIRST"), delay=5.0)
        if verbose:
            print("[ui] 联想第一条：用兜底坐标")
    return True


def result_points(size, limit=3):
    """搜索结果页的候选点击坐标；节点找不到时退回坐标表。"""
    rows = result_rows(dump_nodes(), size, limit=limit)
    if rows:
        return [r["center"] for r in rows]
    x = profile_point("RESULT_X")[0]
    return [(x, profile_point(k)[1]) for k in ("RESULT_Y1", "RESULT_Y2", "RESULT_Y3")][:limit]
