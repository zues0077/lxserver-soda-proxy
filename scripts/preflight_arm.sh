#!/usr/bin/env bash
# 汽水取链代理 —— ARM 主机前置体检（只读，不改任何系统配置）
#
# 用法：bash scripts/preflight_arm.sh
#
# 判定标准（全部通过才有必要往下走）：
#   1. 用户态是 aarch64（不能是 armv7l）—— redroid 的 arm64 镜像是原生 ARM 代码
#   2. 内核里有 binder **文件系统**（binderfs），只有 binder 设备不够
#   3. 内存 >= 3.5G
#   4. 有 docker
#
# 只读：最多执行一次 `modprobe binder_linux`（加载模块，不改持久配置）。
# 全部通过 → exit 0；有 FAIL → exit 1；只有 WARN → exit 0。

set -u

FAILS=0
WARNS=0

pass() { printf '  [PASS] %s\n' "$1"; }
fail() { printf '  [FAIL] %s\n' "$1"; FAILS=$((FAILS + 1)); }
warn() { printf '  [WARN] %s\n' "$1"; WARNS=$((WARNS + 1)); }
info() { printf '  [INFO] %s\n' "$1"; }

echo "== 1. 架构与内核 =="
ARCH="$(uname -m)"
case "$ARCH" in
  aarch64) pass "uname -m = $ARCH（64 位 ARM，符合）" ;;
  armv7l)  fail "uname -m = armv7l（32 位用户态，跑不了 arm64 镜像；需要换 64 位系统）" ;;
  *)       fail "uname -m = $ARCH（不是 ARM，x86 上转译跑不了汽水，见 P-1152）" ;;
esac
KREL="$(uname -r)"
info "内核版本 = $KREL"
# binderfs 自 Linux 5.0 起进主线；4.4 一定没有，4.19 要看是否 Android 分支反移植
KMAJ="$(echo "$KREL" | cut -d. -f1)"
KMIN="$(echo "$KREL" | cut -d. -f2)"
if [ "$KMAJ" -lt 4 ] || { [ "$KMAJ" -eq 4 ] && [ "$KMIN" -lt 14 ]; }; then
  fail "内核 ${KMAJ}.${KMIN} 太老，redroid 至少要 4.14（5.4+ 更稳）"
elif [ "$KMAJ" -eq 4 ] && [ "$KMIN" -lt 19 ]; then
  fail "内核 4.x（${KMAJ}.${KMIN}）基本没有 binderfs，需要换内核"
elif [ "$KMAJ" -eq 4 ]; then
  warn "内核 4.19：只有 Android 分支才反移植了 binderfs，靠下面的实际检测定"
fi

echo "== 2. binder / binderfs（决定 redroid 能否启动）=="
if grep -qw binder /proc/filesystems 2>/dev/null; then
  pass "/proc/filesystems 已含 binder"
else
  info "内核未注册 binder，尝试加载 binder_linux 模块（只读操作）"
  if command -v modprobe >/dev/null 2>&1; then
    modprobe binder_linux 2>/dev/null || true
  fi
  if grep -qw binder /proc/filesystems 2>/dev/null; then
    pass "modprobe binder_linux 后可注册 binder（记得写进 /etc/modules-load.d/ 让它开机自动加载）"
  else
    fail "内核没有 binder/binderfs —— 这是硬阻塞，redroid 起不来（换内核或换机器）"
  fi
fi

# 真正能不能挂：redroid 启动时会 mount -t binder
if grep -qw binder /proc/filesystems 2>/dev/null; then
  TB="$(mktemp -d 2>/dev/null || echo /tmp/binderfs.$$)"
  if mount -t binder binder "$TB" 2>/dev/null; then
    CNT="$(ls "$TB" 2>/dev/null | wc -l)"
    pass "binderfs 可挂载（$TB 下出现 $CNT 个节点）"
    umount "$TB" 2>/dev/null || true
  else
    warn "binderfs 挂载测试失败（可能是权限不足，用 sudo 重跑本脚本再确认）"
  fi
  rmdir "$TB" 2>/dev/null || true
fi

echo "== 3. cgroup 版本 =="
CGT="$(stat -fc %T /sys/fs/cgroup 2>/dev/null || echo unknown)"
case "$CGT" in
  cgroup2fs) warn "纯 cgroup v2（$CGT）：部分 redroid 版本对其支持不完整，起不来时先看这条" ;;
  tmpfs)     pass "cgroup v1（$CGT），兼容性最好" ;;
  *)         warn "cgroup 类型未知（$CGT）" ;;
esac

echo "== 4. 内存与磁盘 =="
MEM_KB="$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
MEM_MB=$((MEM_KB / 1024))
if [ "$MEM_MB" -ge 3500 ]; then
  pass "内存 ${MEM_MB}MB"
elif [ "$MEM_MB" -ge 2000 ]; then
  warn "内存只有 ${MEM_MB}MB：Android 15 系统本身要吃 1.2~1.5G，随时可能 OOM（建议 4G+）"
else
  fail "内存只有 ${MEM_MB}MB，跑不动"
fi
AVAIL_MB="$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
info "当前可用 ${AVAIL_MB}MB"

DF_LINE="$(df -Pm / 2>/dev/null | awk 'NR==2{print $4}')"
info "根分区可用 $((DF_LINE / 1024))GB（redroid 数据目录建议留 10G+）"

echo "== 5. Docker =="
if command -v docker >/dev/null 2>&1; then
  DVER="$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"
  if [ "$DVER" = "unknown" ]; then
    warn "有 docker 命令但连不上 daemon（可能需要 sudo 或加 docker 组）"
  else
    pass "docker $DVER"
    DRV="$(docker info --format '{{.Driver}}' 2>/dev/null || true)"
    DROOT="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"
    info "storage driver=$DRV data-root=$DROOT"
    case "$DRV" in
      overlay2|btrfs) : ;;
      *) warn "storage driver=$DRV，redroid 官方只验证过 overlay2" ;;
    esac
  fi
else
  fail "没有 docker"
fi

echo "== 6. 端口 5555（adb） =="
if command -v ss >/dev/null 2>&1; then
  if ss -ltn 2>/dev/null | grep -q ':5555 '; then
    warn "5555 已被占用（已有别的 adb/redroid 在跑？确认不要冲突）"
  else
    pass "5555 空闲"
  fi
else
  info "没有 ss 命令，跳过端口检查"
fi

echo
if [ "$FAILS" -gt 0 ]; then
  echo "结论：不可行（$FAILS 项 FAIL，$WARNS 项 WARN）"
  exit 1
fi
echo "结论：可以起 redroid（$WARNS 项 WARN，按需处理）"
echo "下一步：bash scripts/deploy_redroid.sh --apk /path/to/luna.apk"
