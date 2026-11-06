#!/usr/bin/env bash
# 在 ARM 主机上起 redroid（原生 arm64）并装好汽水 + ADBKeyboard
#
# 用法：
#   bash scripts/deploy_redroid.sh --apk /path/to/luna.apk [--kbd /path/to/ADBKeyboard.apk]
#
# 参数（都可用环境变量覆盖）：
#   --apk    汽水 APK（必填）
#   --kbd    ADBKeyboard APK（可选；没有就跳过，中文搜索会不可用）
#   --data   redroid 数据目录        DATA=/vol1/docker/redroid-soda/data
#   --name   容器名                  NAME=soda-android
#   --port   adb 端口                PORT=5555
#   --image  镜像                    IMAGE=redroid/redroid:15.0.0-latest
#   --mem    内存上限                MEM=3g
#
# 说明：
# - 官方镜像是多架构 manifest，ARM 主机上 docker pull 拿到的是**原生 arm64**，
#   不是转译 —— 这是它和 x86 方案最本质的区别（x86 转译必崩，见 P-1152）。
# - 参数 width/height/dpi 与真机平板对齐，代理里的点击坐标才能直接复用。
# - 起完容器不会自动登录：汽水要**在容器里扫码登录一次**，见脚本末尾提示。

set -euo pipefail

APK=""
KBD=""
NAME="${NAME:-soda-android}"
DATA="${DATA:-/var/lib/redroid-soda/data}"
PORT="${PORT:-5555}"
IMAGE="${IMAGE:-redroid/redroid:15.0.0-latest}"
MEM="${MEM:-3g}"

while [ $# -gt 0 ]; do
  case "$1" in
    --apk)   APK="${2:-}";   shift 2 ;;
    --kbd)   KBD="${2:-}";   shift 2 ;;
    --data)  DATA="${2:-}";  shift 2 ;;
    --name)  NAME="${2:-}";  shift 2 ;;
    --port)  PORT="${2:-}";  shift 2 ;;
    --image) IMAGE="${2:-}"; shift 2 ;;
    --mem)   MEM="${2:-}";   shift 2 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

[ -n "$APK" ] || { echo "缺少 --apk（汽水 APK 路径）" >&2; exit 2; }
[ -f "$APK" ] || { echo "APK 不存在：$APK" >&2; exit 2; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
fi

echo "== 0. 前置检查 =="
if [ "$(uname -m)" != "aarch64" ]; then
  echo "当前主机不是 aarch64（$(uname -m)），本脚本只适用于 ARM 主机" >&2
  exit 1
fi
grep -qw binder /proc/filesystems || {
  echo "内核没有 binder/binderfs，redroid 起不来。先跑 scripts/preflight_arm.sh" >&2
  exit 1
}

echo "== 1. 拉镜像（arm64 原生，约 800MB）=="
$SUDO docker pull "$IMAGE"

echo "== 2. 起容器 $NAME（数据目录 $DATA）=="
$SUDO mkdir -p "$DATA"
$SUDO docker rm -f "$NAME" >/dev/null 2>&1 || true
$SUDO docker run -d --name "$NAME" \
  --privileged \
  --restart unless-stopped \
  --memory "$MEM" \
  -p "$PORT:5555" \
  -v "$DATA:/data" \
  "$IMAGE" \
  androidboot.hardware=redroid \
  androidboot.redroid_width=1200 \
  androidboot.redroid_height=1848 \
  androidboot.redroid_dpi=240 \
  androidboot.redroid_gpu_mode=guest \
  androidboot.redroid_fps=30

echo "== 3. 等 adb 与系统启动 =="
export ADB_SERVER_PORT="${ADB_SERVER_PORT:-5037}"
ADB="adb"
command -v adb >/dev/null 2>&1 || {
  echo "本机没有 adb 命令。装一个（Debian/Ubuntu: sudo apt install android-tools-adb）后重跑本脚本的第 3 步。" >&2
  exit 1
}

for i in $(seq 1 60); do
  $ADB connect "127.0.0.1:$PORT" >/dev/null 2>&1 || true
  STATE="$($ADB -s "127.0.0.1:$PORT" get-state 2>/dev/null || true)"
  BOOT="$($ADB -s "127.0.0.1:$PORT" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
  if [ "$STATE" = "device" ] && [ "$BOOT" = "1" ]; then
    echo "  系统已启动（第 ${i} 次探测）"
    break
  fi
  sleep 2
  if [ "$i" -eq 60 ]; then
    echo "  等了 120 秒仍未就绪，看日志：$SUDO docker logs --tail 80 $NAME" >&2
    exit 1
  fi
done

$ADB -s "127.0.0.1:$PORT" root >/dev/null 2>&1 || true
sleep 2
$ADB connect "127.0.0.1:$PORT" >/dev/null 2>&1 || true

echo "== 4. 装汽水 =="
$ADB -s "127.0.0.1:$PORT" install -r "$APK"

if [ -n "$KBD" ] && [ -f "$KBD" ]; then
  echo "== 5. 装 ADBKeyboard（中文输入用）=="
  $ADB -s "127.0.0.1:$PORT" install -r "$KBD" || true
  $ADB -s "127.0.0.1:$PORT" shell ime enable com.android.adbkeyboard/.AdbIME >/dev/null 2>&1 || true
  $ADB -s "127.0.0.1:$PORT" shell ime set com.android.adbkeyboard/.AdbIME >/dev/null 2>&1 || true
fi

echo
echo "== 完成。下一步（手动，一次）=="
echo "  1) 在容器里打开汽水并扫码登录："
echo "       $ADB -s 127.0.0.1:$PORT shell am start -n com.luna.music/com.luna.biz.main.main.MainActivity"
echo "     没有画面就看截图：$ADB -s 127.0.0.1:$PORT exec-out screencap -p > /tmp/soda.png"
echo "  2) 登录后启动代理并自检（注意 SODA_TIMEOUT 要放大，ARM 上慢路径更久）："
echo "       SODA_SERIAL=127.0.0.1:$PORT SODA_TIMEOUT=180 python -m soda_proxy.server"
echo "       curl -s http://127.0.0.1:18090/health"
echo "       curl -s 'http://127.0.0.1:18090/track/<track_id>?name=<歌名>&fast=1'"
echo "  3) 在 lxserver 管理页「取链代理」填 http://<本机IP>:18090，保存并测试"
