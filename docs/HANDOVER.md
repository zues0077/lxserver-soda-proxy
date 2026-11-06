# 交接总览（新任务先读这份）

> 最后更新：2026-11-06 11:00（本次会话结束时）
> 相关手册条目：**P-1146 / P-1147 / P-1148 / P-1150 / P-1152 / P-1154 / P-1155**
> 进度库登记：`<进度库>/progress/services/soda-proxy.md`（更细的实测流水）

---

## 1. 这个项目要解决什么

汽水的**版权受限曲**（VIP / 极高音质）在 lxserver 里一直只有 60 秒试听。

根因不是「没登录」「网速慢」，而是：

| 通道 | 实测结果 |
|---|---|
| h5 `track_v2` | 60.001s 试听，`PlayAuth` 长度 0（不下发密钥） |
| SEO `seo_track` | 同上 |
| PC 签名器（wine + bdms.node） | 拿到的是 CENC 加密流，**平台不下发 `spade_a`** → 解不开 |
| **手机端 App** | `video_model.video_list[].encrypt_info.spade_a` 每档都带 → **能解整曲** |

所以结论只有一条路：**让官方手机 App 自己取链，服务端只把结果捡回来**。

这也是「为什么不做手机端签名器」的答案：mssdk（`X-Argus`/`X-Gorgon`/`X-Ladon`）在
`libsscronet.so` 里逐请求生成、和设备指纹绑定、配 `libnpth.so` 反调试；而且**权益判定在客户端侧**
（领畅听前后服务端响应完全一致）。复刻它成本极高且未必有用。代理**绕过**签名，而不是复制签名。

---

## 2. 工作原理（三句话）

```
1. 一台常开的 Android 设备上装着官方汽水 App（真机 或 ARM 主机上的 redroid）
2. proxy 用 adb 指挥它「搜索 → 点歌」→ App 自己签名取链 → 结果落进
   recent_played_<uid>.db 的 cached_playable 表
3. proxy 读那张表，把 (main_url, spade_a) 交给 lxserver，走现成的 CENC 解密管线
```

**全程不碰签名、不碰反调试、不抓包、不 hook。**

---

## 3. 数据从哪读（最容易搞错的一处）

```
/data/data/com.luna.music/databases/recent_played_<uid>.db
  table cached_playable(id, queueId, playableType, playableJson)
    id                              = track_id（数字，与搜索/深链用的同一个）
    playableJson → track.player_info.video_model
                    ↑ 值是 JSON **字符串**，必须二次 parse
                    → video_list[].main_url  +  encrypt_info.spade_a
```

⚠️ **不要读 `player_<uid>.db` 的 `player_info` 表** —— 它只在「队列预取」场景写，
单曲播放完全不写。早期就在这里白耗了很久。

队列容量 30~37 条，**一次「搜索→点歌」会批量写入**（实测 30 → 37）。

---

## 4. 触发方式：必须「搜索 → 点歌」

| 方式 | 结果 |
|---|---|
| 深链 `luna://luna.com/playing?track_id=` | 能播，但**不进队列**（等 28s 队列条数不变）→ 拿不到取链结果 |
| **搜索歌名 → 点结果 → 播放** | ✅ 队列立即写入，目标 track_id 出现 |

搜索兜底三个必踩点：

1. **必须先 `force-stop` 冷启动** —— App 停在结果页时右上角 `(1150,70)` 是「×」不是搜索图标，
   点下去只会清空、不会跳转；
2. **歌名要去括号** —— 搜「稻香（杰伦）」出不来，搜「稻香」才有；
3. **必须等 `video_list` 写完** —— App 先写记录、异步补 `video_model`，
   只判断 id 存在会拿到半成品（报「有记录但没有可用 video_list」）。

中文输入只能用 **ADBKeyboard**（`adb shell input text` 会整段丢弃中文）；
`ime enable/set` 在 Android 9 上必须走 `su`。

---

## 5. 当前代码状态

### lxserver（主仓库，`main`）

| commit | 内容 |
|---|---|
| `fae2de0` | 新增 `sodaProxy.ts` + `soda.ts` 接入（通道顺序、fast、预热） |
| `598d092` | 管理页配置入口 + `test/sodaProxy.test.ts` 6 例 + 诊断字段 |

通道顺序：

```
代理快速档（fast=1，1~7s） → 签名器 → 明文 h5/SEO
        ↓ 都拿不到整曲
   后台预热（60~90s）→ 用户重试时命中缓存、秒出整曲
```

四个反直觉但必须这么写的点（`docs/SESSION-2026-11-06.md` 有详细理由）：

1. 不能用 `musicAccount/http` 的 `request`（会挂音乐出口代理，打不到局域网）；
2. 主链路只等 `fast=1`，慢路径绝不进主请求；
3. 代理探测必须放在「仅试听 5 分钟」负缓存**之前**；
4. 前置 `/health` 探活（60s 缓存），设备掉线时不白等 9 秒。

**未配置 `qishui.proxyUrl` 时行为完全不变**（零请求零延迟）。

### soda-proxy（本仓库）

```
soda_proxy/
  config.py     运行时配置（环境变量 + UI 坐标 + 超时）
  device.py     adb 交互（串行锁、冷启动、搜索、输入、root 自适应）
  queue.py      读 cached_playable（拉库/解析/提流）
  resolver.py   track_id -> streams（队列命中 / 搜索兜底 / fast=1）
  server.py     HTTP 服务（标准库，零第三方依赖）
cli.py
scripts/        preflight_arm.sh / deploy_redroid.sh / docker-entrypoint.sh
Dockerfile + docker-compose.yml
```

接口：

```
GET /health
GET /track/<track_id>?name=<歌名>&fast=1&timeout=<秒>
GET /queue?limit=50
```

`fast=1` 只查队列（1~7s），未命中直接 404；不带它才走搜索兜底（60~90s）。

---

## 6. 宿主：三种结论

| 宿主 | 结论 |
|---|---|
| 已 root 的 Android 平板/手机 | ✅ 可用，所有实测都在这里做的 |
| **ARM 主机 + redroid** | ✅ 理论上就该跑这里（镜像原生 arm64），**待实机验证** |
| x86 主机 + redroid（NDK translation / Houdini） | ❌ **不可行**，两次硬失败，别再试 |

### x86 为什么不行（已实测，别再投入）

汽水 APK `primaryCpuAbi=arm64-v8a`、`secondaryCpuAbi=null`，纯 arm64，x86 必须转译：

- **NDK translation（Android 15）**
  `UnsatisfiedLinkError: lib/arm64/libsafe-mode-native-lib.so is for EM_AARCH64 (183)
  instead of EM_X86_64 (62)`，崩在 `MainApplication.attachBaseContext` ——
  字节 SafeMode SDK 在最早期就 `System.loadLibrary` 加载 arm64 so，转译层没接住，绕不开。
- **Intel Houdini（Android 13）**：crash buffer 空，tombstone 里 `SIGSEGV / SEGV_MAPERR / zygote64`。

容器本身完全正常（`abilist` 含 `arm64-v8a`、`native.bridge=libnb.so`、adb/root 通、APK 装成功 51s），
**卡点纯粹在 arm64 native 库转译**。x86 那台（N100）前置条件其实全满足：
内核 6.18 内置 `binder`、`binfmt_misc` 已挂载、fuse、`/dev/kvm` 都有。

### ARM 为什么行（已核实镜像，未实机）

```
redroid/redroid:15.0.0-latest          →  amd64(883MB) / arm64(821MB)
redroid/redroid:15.0.0_64only-latest   →  amd64(730MB) / arm64(694MB)
```

**多架构 manifest，ARM 主机 pull 下来就是原生 arm64**，不是转译。

所以「RK3399 / RK3566 能不能用」的答案**不取决于芯片，取决于内核有没有 binderfs**：

- 内核 4.4（RK3399 常见 BSP）→ 一定没有（binderfs 5.0 才进主线）
- 4.19 → 只有 Android 分支反移植了才有
- 5.10 / 6.1 主线 → 大概率有

判定就跑 `scripts/preflight_arm.sh`（只读，逐项 PASS/FAIL）。

---

## 7. 待办（按顺序）

1. **ARM 实机验证**（设备当前不在线）
   ```bash
   bash scripts/preflight_arm.sh
   bash scripts/deploy_redroid.sh --apk luna.apk --kbd ADBKeyboard.apk --data <数据目录>
   # 容器内扫码登录一次
   docker compose up -d --build
   # lxserver 管理页「取链代理」填 http://<ARM机IP>:18090
   ```
   要验的：binderfs、内存、App 启动耗时、搜索兜底真实时长、登录风控、容器内 adb + host 网络。
2. **真实设备上跑一次端到端**：`/track/<id>` → 下载 → 解密 → AAC 首元素判据（脚本在 `docs/` 引用里）。
3. **压测并发**：单设备串行，要确认 lxserver 侧不会同时打多首（目前靠 `warmUp` 去重兜着）。
4. **未做的**：电量/温度长期观察（redroid 比真机省心，但也别让 NAS 风扇起飞）。

---

## 8. 安全与边界（必须遵守）

- **本仓库是公开仓库**：不放任何密钥、Cookie、token、内网密码；IP 一律写 `192.168.1.x` 占位。
- 设备密码、SSH 凭据存放在本机操作手册的**加密数据区**（DPAPI），不落明文文件。
- 代理服务只应监听内网；它能让任何人指挥那台设备点歌，**不要暴露到公网**。
- 这条路用的是「账号在手机端本来就有的能力」，**不是破解会员**。账号没有的权益，代理也拿不到。
- 单设备串行 + 长期自动操作，存在账号风控风险，自行评估。

---

## 9. 踩过的坑（省你时间）

| 坑 | 真相 |
|---|---|
| 抓包只看到 CONNECT、解不开 TLS | **Cronet 不走系统代理**，流量压根没进代理，不是证书问题 |
| 切歌时抓不到 `/luna/` 请求 | App 是**预取队列**，队列里有的歌直接读本地，不发请求 |
| 读 `player_info` 表读不到 | 取链结果在 `cached_playable`，不是 `player_info` |
| 深链播放后队列不变 | 深链不走正常播放队列，必须「搜索→点歌」 |
| 点搜索没反应 | 截图是 679×1024，实际屏 1200×1848，**坐标要 ×1.77** |
| `input text` 输入中文变空 | 必须 ADBKeyboard + 广播 |
| frida 一 hook 就闪退 | 别覆盖 `CronetUrlRequest.$init`；别用 `C.start.implementation + o.call(this)`（无限递归） |
| redroid 里报 `su: not found` | adbd 本身就是 root，没有 su；代码已做 `id -u` 探测 |
| `git reset --hard origin/main` 后文件消失 | fetch 失败时 `origin/main` 是**旧的**，reset 会把本地新提交一起抹掉（本次真的踩了，靠远端恢复） |
