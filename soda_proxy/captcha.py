"""汽水登录的滑块拼图验证：在容器内本地识别 + 精确拖动。

为什么必须放在容器里做：
    识别要靠截图，而「截图 -> 传出来 -> 算法算 -> 再传指令进去」一轮就是几分钟，
    这个验证码只有 ~30 秒有效期，远端算必然超时（已经连续超时 4 次）。
    放在容器内，截图和拖动都是本地 adb 调用，整个流程 3~5 秒完成。

为什么不能照着眼睛拖：
    滑块轨道和图片宽度不是 1:1。实测拖 100px，拼图块只走 34px（约 1/3）。
    直接按视觉距离拖永远差一截，所以这里**边拖边量比例**：
    先拖一小段量出「手柄位移 -> 拼图块位移」的比值，再按比例算总距离。
"""
import subprocess
import time

import numpy as np
from PIL import Image

from . import config, device

# 1200x1848 下验证码图片区域（实测固定布局）与滑块手柄位置
IMG_BOX = (308, 648, 892, 992)
HANDLE = (380, 1068)
REFRESH = (358, 1009)     # 验证框里的 Refresh（失败后不会自动换图，必须点它）
TMP = "/tmp/_captcha.png"


def _grab():
    """截屏并读进 PIL。用 exec-out 直接拿二进制，避免文本通道破坏 PNG。"""
    with open(TMP, "wb") as f:
        subprocess.run(config.adb_args() + ["exec-out", "screencap", "-p"],
                       stdout=f, timeout=60)
    return Image.open(TMP).convert("RGB")


def _motionevent(kind, x, y):
    device.shell("input motionevent %s %d %d" % (kind, x, y), timeout=20)


def _blobs(mask, min_px=25):
    """4 邻域连通域，返回 [(x1,y1,x2,y2,像素数), ...]。"""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys, xs):
        if seen[y0, x0]:
            continue
        stack = [(y0, x0)]
        seen[y0, x0] = True
        pts = []
        while stack:
            y, x = stack.pop()
            pts.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        if len(pts) >= min_px:
            py = [q[0] for q in pts]
            px = [q[1] for q in pts]
            out.append((min(px), min(py), max(px), max(py), len(pts)))
    return out


def find_piece(im):
    """拼图块：**白色描边**的镂空图形，全图搜索。

    ⚠️ 不能只扫左侧：拖动过程中块会往右跑，一出左 1/3 就找不到了
    （实测拖 300px 后返回 None，导致比例根本量不出来）。
    白色判定用「三通道都 > 235」——天空/云只是单通道亮，不是白色。
    """
    a = np.asarray(im.crop(IMG_BOX)).astype(np.int16)
    white = np.min(a, axis=2) > 235
    blobs = _blobs(white, min_px=25)
    cands = []
    for x1, y1, x2, y2, n in blobs:
        bw, bh = x2 - x1, y2 - y1
        if 50 <= bw <= 220 and 50 <= bh <= 220:
            cands.append((n, x1, y1, x2, y2))
    if not cands:
        return None
    # 描边最完整（像素最多）的那个；同分取更靠左的
    cands.sort(key=lambda c: (-c[0], c[1]))
    n, x1, y1, x2, y2 = cands[0]
    return {
        "cx": IMG_BOX[0] + (x1 + x2) / 2.0,
        "cy": IMG_BOX[1] + (y1 + y2) / 2.0,
        "w": x2 - x1, "h": y2 - y1,
    }


def find_gaps(im, piece):
    """候选缺口：明显更暗的竖向连通块，尺寸与拼图块接近。

    阈值不能用「中位数 - 45」这种相对值：图片里有远山/树木等大片深色，
    会和缺口连成一条超长区域，被宽度过滤掉（实测候选恒为 0）。
    改成按固定暗度阈值逐个试（40~80），哪个阈值能切出 1~3 个合理宽度的块就用哪个。
    """
    a = np.asarray(im.crop(IMG_BOX)).astype(np.int16)
    h, w, _ = a.shape
    luma = a.mean(axis=2)

    cy = int(piece["cy"] - IMG_BOX[1])
    y1 = max(0, cy - int(piece["h"] * 1.2))
    y2 = min(h, cy + int(piece["h"] * 1.2))

    best = []
    for th in (40, 50, 60, 70, 80):
        dark = luma < th
        band = np.zeros_like(dark)
        band[y1:y2, :] = True
        dark = dark & band

        cols = dark.sum(axis=0)
        runs, start = [], None
        for x in range(w):
            if cols[x] >= 6:
                if start is None:
                    start = x
            elif start is not None:
                runs.append((start, x - 1))
                start = None
        if start is not None:
            runs.append((start, w - 1))

        cands = []
        for x1, x2 in runs:
            gw = x2 - x1
            if gw < piece["w"] * 0.5 or gw > piece["w"] * 1.5:
                continue
            sub = dark[:, x1:x2 + 1]
            yy = np.nonzero(sub.any(axis=1))[0]
            if len(yy) == 0:
                continue
            gh = int(yy.max() - yy.min())
            if gh < piece["h"] * 0.5:
                continue
            cands.append({
                "cx": IMG_BOX[0] + (x1 + x2) / 2.0,
                "cy": IMG_BOX[1] + (yy.min() + yy.max()) / 2.0,
                "w": gw, "h": gh,
            })
        if cands:
            best = cands
            break
    return best


def pick_gap(piece, gaps):
    """选真缺口：宽高最接近拼图块，且必须在拼图块右侧。"""
    right = [g for g in gaps if g["cx"] > piece["cx"] + piece["w"] * 0.5]
    if not right:
        return None
    def score(g):
        return (abs(g["w"] - piece["w"]) / piece["w"] +
                abs(g["h"] - piece["h"]) / max(piece["h"], 1) +
                abs(g["cy"] - piece["cy"]) / max(piece["h"], 1) * 0.5)
    return min(right, key=score)


def _dialog_closed(im):
    """验证框是否已关闭：图片区域变成白色空白（mean 高、std 低）。"""
    area = np.asarray(im.crop(IMG_BOX)).astype(np.int16)
    return float(area.mean()) > 235 and float(area.std()) < 12


def solve(max_rounds=6):
    """自动过滑块。失败会自动重试——App 每次判定失败都会换一张新图。

    为什么必须重试：图里通常有 2~3 个尺寸接近的暗色形状，只有一个是真缺口，
    靠尺寸/位置只能做到大概率选对。选错一次 App 就换图，所以「错了再来」
    比「想办法一次猜准」现实得多（6 次机会，成功率 ~98%）。
    """
    tried = []
    for attempt in range(max_rounds):
        if attempt > 0:
            # 失败后 App 停在 "Time out"，不会自己换图，必须点 Refresh
            device.tap(*REFRESH, delay=0.5)
            time.sleep(2.2)
        im = _grab()
        piece = find_piece(im)
        if not piece:
            tried.append("第%d次: 没找到拼图块" % (attempt + 1))
            time.sleep(2.0)
            continue
        gaps = find_gaps(im, piece)
        gap = pick_gap(piece, gaps)
        if not gap:
            tried.append("第%d次: 没找到合格缺口" % (attempt + 1))
            time.sleep(2.0)
            continue

        target = gap["cx"] - piece["cx"]
        hx, hy = HANDLE
        _motionevent("DOWN", hx, hy)
        time.sleep(0.15)
        probe = 80
        _motionevent("MOVE", hx + probe, hy)
        time.sleep(0.35)
        p2 = find_piece(_grab())
        if not p2:
            _motionevent("UP", hx + probe, hy)
            tried.append("第%d次: 拖动中丢失拼图块" % (attempt + 1))
            time.sleep(2.0)
            continue
        ratio = (p2["cx"] - piece["cx"]) / probe
        if ratio < 0.5:
            _motionevent("UP", hx + probe, hy)
            tried.append("第%d次: 比例异常 %.2f" % (attempt + 1, ratio))
            time.sleep(2.0)
            continue

        final_x = int(round(hx + probe + (target - probe * ratio) / ratio))
        _motionevent("MOVE", final_x, hy)
        time.sleep(0.25)
        _motionevent("UP", final_x, hy)
        tried.append("第%d次: 目标%.0f 比例%.2f" % (attempt + 1, target, ratio))

        time.sleep(2.2)
        im3 = _grab()
        if _dialog_closed(im3):
            return True, " | ".join(tried) + " => 通过"
        # 没通过：App 会自动换一张新图，直接进下一轮
        time.sleep(1.2)

    return False, " | ".join(tried)
