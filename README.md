# soda-proxy

汽水音乐（soda）**取链代理**：给它一个 `track_id`，它返回这首曲子的**整曲直链 + CENC 密钥**。

```
track_id  →  官方 App 自己播放并取链  →  读 App 的本地缓存  →  {main_url, spade_a}
```

返回的是 `(url, key)`，**不中转音频流** —— 调用方（lxserver）拿现有的 `decryptAudioBufferAsync` 自己解密播放。

## 文档索引（新接手先看这里）

| 文件 | 内容 | 什么时候看 |
|---|---|---|
| **[docs/HANDOVER.md](docs/HANDOVER.md)** | 结论合集：原理、数据从哪读、宿主三选一、待办、踩坑清单 | **第一份**，5 分钟看懂全貌 |
| [docs/SESSION-2026-11-06.md](docs/SESSION-2026-11-06.md) | 会话纪要：每轮怎么被推翻、为什么最后选这条路、用户偏好 | 想知道「为什么」的时候 |
| [docs/PROGRESS-LOG.md](docs/PROGRESS-LOG.md) | 实测流水：接口逐个结果、压测表、端到端帧数、失败模式 | 要查具体数字/原始报错 |

> 2026-11-06 状态：**平板侧全链路已打通并压测 5/5**；ARM 宿主（redroid）**镜像已核实为原生 arm64，
> 等实机验证**。x86 宿主已两次实测判死，别再试。

---

## 为什么不是「手机端签名器」

| 形态 | 结论 |
|---|---|
| PC 端签名器（[lxserver-soda-signer](https://github.com/zues0077/lxserver-soda-signer)） | 能用，但**拿不到整曲**：平台对电脑端只给 60.001s、`PlayAuth` 长度 0 |
| 复刻手机端 mssdk 签名 | **不建议**：`X-Argus/X-Gorgon/X-Ladon` 在 `libsscronet.so`（混淆 native），配套 `libnpth.so` 反调试，且与设备指纹绑定；而且权益判定本就在客户端（按 `playable_range` 决定播哪段），签出来也拿不到整曲 |
| **本项目：让 App 自取链，只读结果** | ✅ 已验证。不碰签名、不碰反调试、不抓包 |

关键认知：**服务端对手机端一律下发整曲 + 完整密钥**，所谓"试听 60 秒"是客户端自己按
`playable_range` 截的。所以只要拿到 App 手里的那份 `video_model`，就能播整曲。

---

## 数据从哪来

App 把**整个播放队列**的取链结果持久化在：

```
/data/data/com.luna.music/databases/recent_played_<uid>.db
  └─ cached_playable(id, queueId, playableType, playableJson)
       id            = track_id
       playableJson  → track.player_info.video_model   ← JSON 字符串，需二次 parse
                         ├─ video_id        (vid, 形如 v0dad6g...)
                         ├─ video_duration  (秒)
                         └─ video_list[]
                              ├─ main_url / backup_url
                              ├─ video_meta { quality, bitrate, size }
                              └─ encrypt_info { encrypt, kid, spade_a }  ← spade_a = CENC 密钥
```

> ⚠️ **不是** `player_<uid>.db` 的 `player_info` 表。那张表只在"队列预取"时写，
> 单曲播放完全不写 —— 早期实现踩过这个坑，读那张表会一直读不到。

读取注意：
- 必须连 `.db-wal` 一起拉（新数据在 WAL 里）；
- 目录下有 `recent_played_0.db`（占位）和 `recent_played_<uid>.db`，**取名字最长的**；
- App 是**先写记录、再异步补 `video_model`** → 必须等到 `video_list` 非空，否则拿到半成品。

---

## 前置条件

1. **一台已 root 的 Android 设备**（真机 / ARM 容器）。adb 能连、`su` 可用。
2. 设备上装了**汽水音乐**（`com.luna.music`）并**已登录**（畅听权益按 App 内的账号走）。
3. 设备上装了 **ADBKeyboard**（`com.android.adbkeyboard`）—— `adb shell input text` 会丢弃中文，
   必须靠它输入歌名。安装后首次需要 `su -c 'ime set com.android.adbkeyboard/.AdbIME'`。
4. 本机有 `adb`。Python ≥ 3.8（**无第三方依赖**）。

---

## 快速开始

```bash
# 1) 连设备（USB 或 adb connect <ip>:5555）
adb devices

# 2) 列一下 App 队列里现有的歌（能读到就说明链路通了）
python cli.py --list

# 3) 取一首队列里已有的（秒级）
python cli.py 7632155073128564782

# 4) 取一首队列里没有的（走搜索兜底，约 60~70 秒）
python cli.py 7676337516358617115 --name 晴天 --json out.json

# 5) 起 HTTP 服务（默认 0.0.0.0:18090）
python -m soda_proxy.server
```

环境变量（全部可选）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SODA_ADB` | `adb` | adb 可执行路径 |
| `SODA_SERIAL` | 空 | 多设备时指定，如 `192.168.1.177:5555` |
| `SODA_PKG` | `com.luna.music` | 包名 |
| `SODA_TIMEOUT` | `90` | 单次取链总预算（秒） |
| `SODA_PICK_WAIT` | `6` | 点一条搜索结果后等队列写入的窗口（秒） |
| `SODA_WORK_DIR` | 临时目录 | 拉下来的数据库落脚点 |

---

## HTTP 接口

### `GET /health`
```json
{"ok": true, "service": "soda-proxy"}
```

### `GET /track/<track_id>?name=<歌名>&fast=1&timeout=<秒>`

`fast=1`：**只查播放队列缓存就返回**，未命中直接回 404，不做搜索兜底。
给取流主链路用（等不起 60~90 秒）；慢路径由调用方在后台单独触发。

**队列命中**（约 1 秒）：
```json
{
  "ok": true,
  "trackId": "7632155073128564782",
  "name": "花鸦",
  "duration": 151.706,
  "source": "queue-cache",
  "streams": [
    {"quality": "highest", "bitrate": 260285, "size": 4935867,
     "main_url": "https://v*.douyinvod.com/...", "spade_a": "<52 字符>", "encrypt": true}
  ]
}
```

**搜索兜底**（60~70 秒）：`source` 为 `"search"`，额外带 `picked: {keyword, index}`。

`streams` 按 `bitrate` 降序；调用方自行按需选档。

> ⚠️ 走慢路径（不带 `fast`）时，客户端超时必须 ≥ 90 秒，否则会被自己的读超时打断。
> 慢路径在 RK3566/RK3399 这类 ARM NAS 上会更慢（实测后补），建议给到 150 秒以上。

### `GET /queue?limit=50`
列出 App 队列里已有的曲目（调试/预热用）。

---

## 两条取链路径

| 路径 | 触发条件 | 耗时 | 说明 |
|---|---|---|---|
| **队列命中** | `cached_playable` 已有该 track 且 `video_list` 非空 | ~1s | 首选，不打扰 App |
| **搜索兜底** | 未命中且请求未带 `fast=1` | 60~70s | 冷启动 → 搜索歌名 → 点结果 → 等队列写入 |

**搜索兜底的三个必踩点**（都已在代码里处理）：

1. **必须冷启动**（`am force-stop` → `am start`）。App 停在结果页时右上角是「×」不是搜索图标，
   点 `(1150,70)` 只会清空、不会进搜索页。
2. **歌名要去括号**：搜「稻香（杰伦）」出不来，搜「稻香」才有。代码里两版都会试。
3. **必须等 `video_list` 写完**（App 异步补），只看 id 会拿到半成品。

> 另：`luna://luna.com/playing?track_id=xxx` 深链虽然能播放，但**不并入播放队列**，
> 队列里不会出现该 track，因此**不能**用作兜底。

---

## UI 坐标与设备适配

坐标集中在 [soda_proxy/config.py](soda_proxy/config.py)，按 **1080×1920 竖屏**实测
（也在 1200×1848 的平板上验证过）。换分辨率/DPI 必须重新校准：

- `SEARCH_ICON` 右上角放大镜
- `SEARCH_BOX` 搜索输入框
- `SUGGEST_FIRST` 联想第一条
- `RESULT_YS_WITH_CARD` / `RESULT_YS_NO_CARD` 结果页前 3 条（两种布局）

> 截图坐标≠屏幕坐标：`adb exec-out screencap` 出来的图常是缩放过的（如 679×1024），
> 点击前要按 `屏幕宽/图宽` 换算。

---

## 部署形态

| 宿主 | 结论 |
|---|---|
| **已 root 的 Android 平板/手机** | ✅ 可用（本项目所有实测都在真机上完成） |
| **ARM 主机上的 redroid 容器**（RK3399 / RK3566 / 树莓派 5 …） | ✅ 镜像原生 arm64，无需转译；**待实机验证**（脚本已备好，见下） |
| **x86 主机上的 redroid**（NDK translation / Houdini） | ❌ **不可行**，见下 |

### 为什么 x86 容器不行（已实测两次，别再试）

汽水 APK 是 **arm64-v8a only**（`secondaryCpuAbi=null`），x86 主机必须靠转译：

- **NDK translation（Android 15）**
  `UnsatisfiedLinkError: lib/arm64/libsafe-mode-native-lib.so is for EM_AARCH64 (183)
  instead of EM_X86_64 (62)`，崩在 `MainApplication.attachBaseContext` ——
  字节 SafeMode SDK 在最早期就用 `System.loadLibrary` 加载 arm64 so，转译层没接住。
- **Intel Houdini（Android 13）**：`SIGSEGV / SEGV_MAPERR`，崩在 `zygote64`。

容器本身没问题（adb、root、`abilist` 含 `arm64-v8a`、`native.bridge=libnb.so` 都正常，
APK 也能装成功），**卡点纯粹在 arm64 native 库转译**。

### ARM 主机部署（redroid，一条命令体检 + 一步起容器）

x86 的死因是**转译**，ARM 上不存在这个问题。
`redroid/redroid:15.0.0-latest` 在 Docker Hub 上是**多架构 manifest**：

```
amd64 (883MB) / arm64 (821MB)
```

也就是说在 ARM 主机上 `docker pull` 拿到的就是原生 arm64 镜像，跑的是真 ARM 指令。

**硬性前置（只有一条最关键：binderfs）**。redroid 启动时要 `mount -t binder`，
只有 `binder` 设备文件不够；内核 4.4 一定没有 binderfs（5.0 才进主线），
4.19 要看是不是 Android 分支反移植的。

```bash
# 1) 体检（只读）
bash scripts/preflight_arm.sh

# 2) 起 Android（原生 arm64）+ 装汽水 + 装 ADBKeyboard
bash scripts/deploy_redroid.sh \
  --apk /path/to/luna.apk \
  --kbd /path/to/ADBKeyboard.apk \
  --data /vol1/docker/redroid-soda/data

# 3) 在容器里扫码登录一次（脚本末尾会打印具体命令）
# 4) 起代理容器
docker compose up -d --build
```

体检脚本会逐项给出 PASS/FAIL，判据是：`uname -m = aarch64`、`/proc/filesystems` 含
`binder`、内存 ≥ 3.5G、有 docker。**有 FAIL 就别往下走**，换内核或换机器。

几个容易踩的点：

- **内存**：4G 起，8G 舒服。2G 板子跑不动（Android 15 系统本身 1.2~1.5G）。
- **代理必须 host 网络**：redroid 的 adb 端口发布在 `127.0.0.1:5555`，桥接网络连不到。
- **慢路径更慢**：平板搜索兜底 60~90 秒，A55/A72 上估计 3~5 分钟，
  所以 `SODA_TIMEOUT` 要放大到 180，`SODA_PICK_WAIT` 放大到 10。
- **redroid 里没有 `su`**：adbd 本身就是 root，代码已做 `id -u` 探测自动切换。
- **坐标要按容器分辨率校准**：`config.py` 的坐标是按 1200×1848 标的，
  部署脚本起容器时用的也是这个分辨率，就是为了直接复用。

---

## 与 lxserver 集成

已在 lxserver 落地（`src/server/extraSources/sodaProxy.ts` + `soda.ts`），通道顺序：

```
代理快速档（fast=1，1~7s） → 签名器 → 明文 h5/SEO
      ↓ 都拿不到整曲
   后台预热（慢路径 60~90s）→ 用户重试时命中缓存、秒出整曲
```

- 命中代理时 `source=proxy`，返回的 `main_url` + `spade_a` 直接进
  **现有的** `decryptAudioBufferAsync` 解密管线（解密逻辑零改动）；
- 主链路**只等** `fast=1`，绝不阻塞等 90 秒的搜索兜底；
- 未配置 `qishui.proxyUrl` 时行为与之前完全一致（一次请求都不发）；
- 配置入口在管理页「汽水签名器」下方的**取链代理**一行，也可用
  `QISHUI_PROXY_URL` 环境变量。

---

## 实测数据（2026-11-06）

| 歌 | 路径 | 耗时 | 时长 | 结果 |
|---|---|---|---|---|
| 花鸦 | 队列命中 | 1.0s | 151.7s | 6 档 |
| 夜曲（杰伦） | 队列命中 | 1.2s | 69.8s | 5 档 |
| 没那种命 | 搜索兜底 | ~70s | 288.8s | 6 档 |
| 晴天 | 搜索兜底 | 68s | 237.3s | 6 档 |
| 稻香（杰伦） | 搜索兜底 | 59s | 74.7s | 5 档 |

端到端（下载 → 解密 → AAC 帧首元素判据）：花鸦 6536 帧 / 没那种命 12440 帧 / 晴天 10224 帧，
**均 100% 合法**。

---

## 已知限制

- 搜索结果前 3 条内没有目标 track 就会失败（同名版本多时可能漏）。
- 走兜底会**冷启动 App**，打断当前播放 —— 用户正在听歌时不合适。
- 返回的 `main_url` 带时效，**拿到后要尽快使用**。
- 单设备串行：同一时刻只处理一个兜底请求（内部有锁）。
- `video_duration` 可能是 60~75 秒，那是歌曲本身时长，不是试听截断
  —— 判断试听要和曲目元数据 `duration` 比对，不能看绝对值。

---

## 目录结构

```
soda_proxy/
  config.py     运行时配置（环境变量 + UI 坐标 + 超时）
  device.py     adb 交互（串行锁、冷启动、搜索、输入、root 自适应）
  queue.py      读 cached_playable（拉库 + 解析 + 提流）
  resolver.py   核心：track_id -> streams（队列命中 / 搜索兜底 / fast 只查队列）
  server.py     HTTP 服务（标准库）
cli.py          命令行入口
scripts/
  preflight_arm.sh      ARM 主机体检（aarch64 / binderfs / 内存 / docker，只读）
  deploy_redroid.sh     起 redroid + 装汽水 + 装 ADBKeyboard
  docker-entrypoint.sh  代理容器入口（先 adb connect 再起服务）
Dockerfile              代理镜像（python + adb，不含 Android）
docker-compose.yml      redroid + 代理 的成对部署（代理用 host 网络）
```

---

## 安全

本服务需要设备的 root/adb 权限，**只应部署在受信任内网**，不要暴露公网。
返回的 `main_url` 与 `spade_a` 属敏感数据，日志里不要打印全文。
