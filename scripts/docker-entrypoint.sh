#!/bin/sh
# 容器入口：先把 adb 连上目标设备，再起 HTTP 服务
set -e

if [ -n "${SODA_SERIAL:-}" ]; then
  case "$SODA_SERIAL" in
    *:*)
      # adb server 首次调用会被拉起；连不上不致命（服务仍能起，只是取链会失败）
      adb connect "$SODA_SERIAL" >/dev/null 2>&1 || true
      ;;
  esac
fi

exec "$@"
