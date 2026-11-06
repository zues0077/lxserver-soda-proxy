# 实测与进度流水（进度库副本）

> 本文是进度库 `progress/services/soda-proxy.md` 的**只读副本**，随本仓库一起留档。
> 原始副本仍以进度库为准；这里是给「新开任务直接 clone 本仓库」时能自洽的最小集合。
>
> 阅读顺序建议：`docs/HANDOVER.md`（结论）→ `docs/SESSION-2026-11-06.md`（为什么）
> → 本文（实测流水与失败模式）。

---

## 汽水取链代理（soda-proxy）—— 方案与实测交接文档

> **代码仓库**：https://github.com/zues0077/lxserver-soda-proxy （2026-11-06 已推送初版，10 个文件）
> 版本：2026-11-06 首次验证通过
> 状态：**协议已验证可用**（5 首压测 + 3 首端到端解密全部通过）；容器化在 x86 主机上**实测不可行**，需 ARM 设备
> 相关手册：P-1144 / P-1146 / P-1147 / P-1148 / P-1150 / P-1151

---

### 1. 一句话结论

**不需要"手机端签名器"。** 让官方 App 自己完成签名与取链，我们只读它写进本地数据库的
`(main_url, spade_a)`，然后交给 lxserver 现有的 `decryptAudioBufferAsync` 解密播放。

链路：`track_id` → adb 让 App 播放 → 读 `cached_playable` → `(url, key)` → 解密 → 整曲。

---

### 2. 为什么不是"签名器"

| 形态 | 可行性 | 原因 |
|---|---|---|
| PC 签名器（现有 `lxserver-soda-signer`） | 可用但拿不到整曲 | 平台对电脑端不下发整曲密钥，只给 60.001s、`PlayAuth` 长度 0 |
| 复刻手机端 mssdk 签名 | **不建议** | `X-Argus/X-Gorgon/X-Ladon` 在 `libsscronet.so`（混淆 native），配套 `libnpth.so` 反调试，且与设备指纹绑定；即使复刻也解决不了权益判定（在客户端按 `playable_range` 决定） |
| **本方案：App 自取链 + 读表** | **已验证** | 完全不碰签名/反调试/抓包 |

---

### 3. 接口契约（建议的 proxy 对外形态）

```
GET /track/{track_id}?name={歌名}
→ 200 {
    "ok": true,
    "trackId": "7632155073128564782",
    "name": "花鸦",
    "duration": 151.706,
    "source": "queue-cache" | "search",
    "streams": [
      { "quality": "highest", "bitrate": 260285, "size": 4935867,
        "main_url": "https://v*.douyinvod.com/...", "spade_a": "<52 字符>", "encrypt": true },
      ...
    ]
  }
→ 4xx/5xx { "ok": false, "error": "..." }
```

- `name` 用于队列未命中时的搜索兜底；**必须是歌名**，带括号的会被自动清洗。
- `streams` 按 `bitrate` 降序，调用方自行按需选档（lxserver 的 `chooseStream` 逻辑可复用）。
- **只返回 URL + 密钥，不中转音频流** —— 这是关键设计，设备负载极低（单次请求 ~100KB）。

---

### 4. 数据来源（核心发现）

**不在 `player_<uid>.db` 的 `player_info` 表**（那套只在队列预取时写，单曲播放完全不写）。

真正的位置：

```
/data/data/com.luna.music/databases/recent_played_<uid>.db
  └─ cached_playable(id, queueId, playableType, playableJson)
       id            = track_id（与搜索/深链用的是同一个数字 ID）
       playableJson  → track.player_info.video_model   ← 值是 JSON 字符串，需二次 parse
                         ├─ video_id        (vid，形如 v0dad6g...)
                         ├─ video_duration  (秒)
                         └─ video_list[]
                              ├─ main_url
                              ├─ backup_url
                              ├─ video_meta { quality, bitrate, size }
                              └─ encrypt_info { encrypt, kid, spade_a }   ← spade_a 是 CENC 密钥
```

实测：队列容 30~37 条，**点歌时批量写入**（一次搜索点歌，30→37）；
`spade_a` 为 52 字符 base64，`resolveAudioKey` 可直接解出 32 位 hex AES key。

读取注意：
- 必须同时拉 `.db` 和 `-wal`（新数据在 WAL 里）。
- 目录下有 `recent_played_0.db`（占位/旧库）和 `recent_played_<uid>.db`，**取名字最长的**。
- App 是**先写记录、再异步补 `video_model`** → 必须等到 `video_list` 非空，否则拿到半成品。

---

### 5. 触发流程

### 5.1 路径 A：队列命中（约 1 秒）
`cached_playable` 里已有该 `track_id` → 直接返回。**这是首选**，不打扰 App。

### 5.2 路径 B：搜索兜底（约 60–70 秒）
```
am force-stop com.luna.music                     ← 必须冷启动
am start -n com.luna.music/com.luna.biz.main.main.MainActivity
等 9s（落在发现页，右上角一定是放大镜）
tap(1150, 70)                                    ← 进搜索页
tap(400, 82)                                     ← 聚焦搜索框
am broadcast -a ADB_INPUT_TEXT --es msg '<歌名>' ← ADBKeyboard 输入中文
tap(220, 139)                                    ← 点第一条联想，进结果页
tap(220, 473) / (220, 353)                       ← 点歌曲条目（两种布局各试前 3 条）
轮询 cached_playable 直到目标 track_id 且 video_list 非空
```

**三个必踩点**：
1. **必须冷启动**。App 停在结果页时 `(1150,70)` 是「×」而不是搜索图标，点下去只清空不跳转。
2. **歌名要去括号**：搜「稻香（杰伦）」出不来，搜「稻香」才有。
3. **必须等 `video_list` 写完**（见第 4 节）。

其他：
- `adb shell input text '中文'` 会**整段丢弃**，必须装 ADBKeyboard；
- `ime set` 在 Android 9 上必须 `su -c`；
- ADBKeyboard 不弹键盘，所以没有"搜索"按钮，只能点联想或点结果条目；
- 截图是 679×1024 而屏幕是 1200×1848，**点击坐标要 ×1.77**。

---

### 6. 实测数据

### 6.1 取链压测（2026-11-06）

| 歌 | 路径 | 耗时 | 时长 | 结果 |
|---|---|---|---|---|
| 花鸦 | 队列命中 | 1.0s | 151.7s | OK 6 档 |
| 夜曲（杰伦） | 队列命中 | 1.2s | 69.8s | OK 5 档 |
| 没那种命 | 搜索兜底 | ~70s | 288.8s | OK 6 档 |
| 晴天 | 搜索兜底 | 68s | 237.3s | OK 6 档 |
| 稻香（杰伦） | 搜索兜底（洗名后） | 59s | 74.7s | OK 5 档 |

### 6.2 端到端解密（下载 → lxserver `decryptAudioBufferAsync` → AAC 首元素判据）

| 歌 | 下载 | 解密 | 帧数 | 判据 |
|---|---|---|---|---|
| 花鸦 | 4935867B | 4883518B | 6536 | **100% = `001`** |
| 没那种命 | 9393619B | 9294038B | 12440 | **100% = `001`** |
| 晴天 | 7720438B | 7638585B | 10224 | **100% = `001`** |

---

### 7. 部署形态：三种宿主的实测结论

| 宿主 | 结论 | 说明 |
|---|---|---|
| **现有 root 平板** | ✅ **可用** | 全部实测都在这台（Android 9 / arm64 / Magisk / spade1099x）上跑通 |
| **246 上的 redroid 容器（x86_64）** | ❌ **不可行** | 见下 |
| ARM 设备（ARM NAS / 树莓派 / 云 ARM） | ⏳ 未实测，理论可行 | 原生 arm64 不需要转译，是容器化的正确方向 |

### redroid 失败细节（重要，别再重复投入）

246 的前置条件其实**全部满足**：内核 6.18 内置 `binder`、`binfmt_misc` 已挂载、`fuse` 有、`/dev/kvm` 有。

但汽水 APK 是 **arm64-v8a only**（`secondaryCpuAbi=null`），而 x86 主机必须靠转译：

- **NDK translation（`erstt/redroid:15.0.0_ndk_AVD`，Android 15）**
  `UnsatisfiedLinkError: lib/arm64/libsafe-mode-native-lib.so is for EM_AARCH64 (183)
  instead of EM_X86_64 (62)`
  字节 SafeMode SDK 在 `Application.attachBaseContext` **最早期**就用 `System.loadLibrary`
  直接加载 arm64 so，转译层没接住 → 启动即崩，**绕不开**。

- **Intel Houdini（`erstt/redroid:13.0.0_houdini_ChromeOS`，Android 13）**
  native `SIGSEGV`（`signal 11, SEGV_MAPERR`，`<anonymous>` 段，崩在 zygote64），
  crash buffer 为空、tombstone 有记录 → 转译层跑不动这个 so。

两个镜像的 Android 侧都正常（adb、root、`abilist` 含 `arm64-v8a`、`native.bridge=libnb.so` 都对），
**卡点纯粹在 arm64 native 库转译**。

> 另注：`pid_max = 4194304`（redroid 文档要求 < 65535），本次未成为阻塞点，但换宿主时要注意。

---

### 8. 已知限制与失败模式

| 现象 | 原因 | 处理 |
|---|---|---|
| 「有记录但没有可用 video_list」 | 读到半成品（App 异步补 `video_model`） | 等到 `video_list` 非空再返回 |
| 搜索后前 6 条都没有目标 track | 同名版本太多 / 歌名带括号 | 洗掉括号再搜；扩大点选范围 |
| 搜索框内容没变、页面没跳 | 没冷启动，点到了「×」 | 强制 `am force-stop` 后再点搜索 |
| 深链 `luna://.../playing` 后队列无变化 | 深链**不并入队列**，只走单曲播放路径 | 不要用深链做兜底，改用搜索点歌 |
| 冷启动打断当前播放 | 设计如此 | 代理场景可接受；用户正在听歌时应避免 |
| 60~75s 的 `video_duration` | 歌曲本身就这么短，不是试听截断 | 判断试听要和 `track.duration` 比对，不能看绝对值 |

---

### 9. 脚本与命令

| 脚本 | 用途 |
|---|---|
| `C:/tmp/qs_proxy3.py` | **代理正式版**。`--list` 列队列；`<track_id> --name 歌名` 取链（含搜索兜底），`--json` 落盘 |
| `C:/tmp/qs_search_id.py <关键词>` | 用免签搜索接口拿 `track_id + 歌名`（模拟 lxserver 输入） |
| `C:/tmp/qs_from_queue.py <track_id>` | 直接从队列导出某 track 的 streams（调试） |
| `C:/tmp/qs_proxy_e2e.cjs` | 端到端：下载 → 仓库解密模块 → 输出 m4a |
| `C:/tmp/qs_verify_key_aac.cjs` | AAC 帧首元素判据（密钥正确性） |
| `C:/tmp/qs_spawn_all.js` + `qs_frida_run.py` | （已不需要）frida hook 版取证，留作对照 |

常用命令：
```bash
# 列队列
python C:/tmp/qs_proxy3.py --list

# 队列命中（秒级）
python C:/tmp/qs_proxy3.py 7632155073128564782

# 搜索兜底
python C:/tmp/qs_proxy3.py 7676337516358617115 --name 晴天 --json C:/tmp/out.json

# 端到端验证
cd E:/AI/lx-music/repo && ./node_modules/.bin/tsx C:/tmp/qs_proxy_e2e.cjs C:/tmp/out.json highest
node C:/tmp/qs_verify_key_aac.cjs C:/tmp/qs_proxy_out..m4a
```

---

### 10. 下一步建议

1. **接进 lxserver**：新增 `sodaProxy` 通道，`getAudioUrl` 在 h5/seo 都只给试听时，
   调 proxy 的 `/track/{id}` 拿 `(url, key)`，走现有的 `registerSodaKey` + 解密管线（**解密逻辑零改动**）。
2. **宿主先沿用平板**：先让功能跑起来（协议已稳），把平板改成"无头常开"
   （关屏 + `svc power stayon usb` + `adb tcpip 5555`），成本 0。
3. **容器化走 ARM 主机**：x86 转译这条路已有两次硬失败记录，不要再试；
   换 ARM NAS / 树莓派 / 云 ARM 实例时，redroid 的部署参数可复用本次的（`--privileged`、
   `androidboot.redroid_width/height/dpi`、`gpu_mode=guest`）。
4. **仓库拆分**：`lxserver-soda-signer` 保持只管 PC；proxy 单独建仓库
   （运行时 = adb + Android，和 Node+wine 完全不同；且需要设备 root 权限）。

---

### 11. 2026-11-06 下午补充：lxserver 侧已接入 + ARM 部署件已备好

### 11.1 lxserver 接入（已落地，未发版）

`soda.ts` 的通道顺序已改为 **代理快速档 → 签名器 → 明文 h5/SEO**：

| 环节 | 行为 |
|---|---|
| `sodaProxy.ts`（新增） | 直连 node:http（**不走 musicAccount/http**，否则会被 `proxy.music` 出口代理带偏，打不到局域网） |
| 主链路 | 只发 `fast=1`（1~7 秒）。前置 `/health` 探活（60 秒缓存），设备离线时一次请求都不发 |
| 命不中 | 不阻塞。失败文案追加「已在后台通过取链代理预热，约 1 分钟后重新点击可播整曲」 |
| 预热 | `warmUp()` 走慢路径（不带 fast），同一首 3 分钟内只触发一次 |
| 负缓存 | 代理探测放在「仅试听 5 分钟」负缓存**之前**，否则预热成功也会被旧判定挡住 |
| 诊断 | `/api/music/extra/diagnose` 补 `hasProxy / proxyOnline / proxyMessage`，命中时 `source=proxy` |
| 配置 | 管理页「取链代理」输入框（`/api/music/prefetch/proxy`），或环境变量 `QISHUI_PROXY_URL` |

未配置时**行为完全不变**：`isConfigured()` 为假直接返回 null，零请求零延迟。

单测 `test/sodaProxy.test.ts` 6 例（未配置零请求 / fast=1 必带 / 非法 URL 丢弃 /
404 不抛错 / 服务不可达不抛错 / 预热走慢路径且去重），连同既有汽水用例全绿。

### 11.2 ARM 主机：镜像这一关已核实

`redroid/redroid:15.0.0-latest` 是**多架构 manifest**：

```
amd64 (883MB) / arm64 (821MB)
```

ARM 主机上拉下来就是**原生 arm64**，不存在 x86 那种转译失败。
所以「RK3399 / RK3566 能不能用」的答案取决于**内核有没有 binderfs**，不是芯片。

### 11.3 已备好的部署件（设备上线即可执行）

```
scripts/preflight_arm.sh    只读体检：aarch64 / binder / binderfs 可挂载 / cgroup / 内存 / docker / 5555
scripts/deploy_redroid.sh   拉镜像 → 起容器(1200x1848 dpi240 gpu=guest) → 等 boot → adb root
                            → 装汽水 → 装 ADBKeyboard → 打印登录与自检命令
Dockerfile + docker-compose.yml   代理镜像（python+adb）与成对部署（代理用 host 网络）
```

体检判据：`uname -m = aarch64`、`/proc/filesystems` 含 `binder`、内存 ≥ 3.5G、有 docker。
内核 4.4 一定没有 binderfs（5.0 才进主线），4.19 要看是否 Android 分支反移植。

### 11.4 换宿主时必须注意的三处

1. **redroid 里没有 `su`**：adbd 本身就是 root。`device.py` 已改成先探 `id -u`，
   真机走 `su -c`、容器直接执行，`su` 缺失时自动退回直跑（否则换宿主必报 `su: not found`）。
2. **超时要放大**：ARM 上冷启动 190MB/33 dex 的 App 更慢，`SODA_TIMEOUT=180`、`SODA_PICK_WAIT=10`。
3. **坐标沿用 1200×1848**：部署脚本起容器时就用这个分辨率，`config.py` 里的坐标无需改。

### 11.5 仍未验证

- ARM 实机（设备当前不在线）：binderfs、内存、启动耗时、搜索兜底真实时长、登录风控。
- 代理在容器里的 `adb connect 127.0.0.1:5555` + host 网络组合（脚本已写，未实跑）。
