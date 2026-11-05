# 汽水取链代理 —— 容器镜像
#
# 只需要 python3 + adb。它**不跑 Android**：Android 由同机的 redroid 容器提供，
# 代理通过 adb 让那里的官方 App 自己取链。
#
# 构建：docker build -t soda-proxy .
# 运行：见 docker-compose.yml（必须用 host 网络，才能连到 127.0.0.1:5555 上的 redroid）
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends android-tools-adb ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY soda_proxy ./soda_proxy
COPY cli.py ./
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
    SODA_TIMEOUT=180 \
    SODA_WORK_DIR=/tmp/soda-proxy

EXPOSE 18090

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "soda_proxy.server"]
