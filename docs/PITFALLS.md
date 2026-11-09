# 踩坑与误操作备查（PITFALLS）

> 每条格式固定：**现象**（你会看到什么）→ **真因**（不是你以为的那个）→ **正确做法**。
> 标 🔴 的是「看上去很合理、但一照做就出错」的那种，优先级最高。
> 本文只写**已经实际踩过并验证**的，不写推测。

---

## 0. 最贵的三条（先看这个，能省几小时）

### 🔴 0.1 读 `player_info` 表拿取链结果 → 永远读不到

- **现象**：想按 track 读 App 的取链缓存，`player_<uid>.db` 的 `player_info` 表只有几十行，
  而且播放新歌时**不增长**。反复怀疑「是不是 hook 没生效 / 缓存没写」。
- **真因**：那张表**只在「队列预取」场景写**，单曲播放完全不写。取链结果实际落在
  另一个库的另一张表。
- **正确做法**：

  ```
  /data/data/com.luna.music/databases/recent_played_<uid>.db
    table cached_playable(id, queueId, playableType, playableJson)
      id                            = track_id（数字，与搜索/深链同一个）
      playableJson → track.player_info.video_model
                      ↑ 值是 JSON 字符串，必须**二次 parse**
                      → video_list[].main_url + encrypt_info.spade_a
  ```
  注意同一个目录下还有 `recent_played_0.db`（占位/旧库），**取文件名最长的那份**（带 uid 的）。

### 🔴 0.2 深链打开歌曲 → 拿不到取链结果

- **现象**：`am start -d 'luna://luna.com/playing?track_id=xxx'` 能正常播放，
  但 `cached_playable` 条数不变、等 28 秒也不变。看起来「播放了却没缓存」。
- **真因**：深链**不并入正常播放队列**（`daily_mix` / `search` 队列），走的是独立路径，
  所以不会写队列缓存；`cached_play_source.currentPlayableId` 也不更新。
- **正确做法**：必须 **搜索歌名 → 点搜索结果 → 播放**。实测一次点歌队列从 30 涨到 37。
  深链只适合「验证某首歌能不能播」，不能用来取链。

### 🔴 0.3 抓包只看到 CONNECT、解不开 TLS → 别再折腾证书

- **现象**：设备代理指向 Reqable/Charles，列表里只有零星 `CONNECT host:443`（几百字节，无路径无 body），
  抓不到任何 `/luna/` 请求。于是开始怀疑证书没装、SSL Pinning、Android 版本。
- **真因**：**Cronet 不走系统 `http_proxy`**。App 的真实请求是直连的，压根没进代理。
  实测证据：SSL bypass 的 `[CB] verify cb` 计数为 **0**，而 `/proc/net/tcp` 里确实有到代理端口的
  ESTABLISHED —— 说明连上了但没被使用。
- **正确做法**：汽水这类 Cronet App，抓包这条路基本是死的。要拿请求就走
  「读 APK 找接口」+「hook JSON 解析」+「读本地数据库」，别在 MITM 上耗时间。

---

## 1. 数据层

| # | 现象 | 真因 | 正确做法 |
|---|---|---|---|
| 1.1 | 用数字 `track_id` 调 `/luna/media_v2` 返回 200 但 body 为空 | App 里存的是 **两套 ID**：数字 `track_id` 与 `vid`（`v0300fg...`）。`player_info.mediaId` 存的是 **vid** | 先确认接口要哪个 ID。feed 的 track 同时给 `id`（数字）和 `vid` |
| 1.2 | 记录已出现、但 `video_list` 为空 → 报「有记录但没有可用 video_list」 | App 是**先写记录、再异步补 `video_model`** | 轮询时必须等到 `video_list` 非空才算命中，不能只判断 id 存在 |
| 1.3 | 搜「稻香（杰伦）」搜不到 | 搜索结果不含带括号的完整名 | 搜之前用 `[（(【\[]...?[）)】\]]` 清掉后缀；脚本里最好两种都试 |
| 1.4 | 某首歌唱到 69.8s 就结束，以为是试听截断 | `video_duration` 是**音频流时长**，这首歌本身就是 69.8s | 判试听要跟曲目元数据的 `duration` 比，不能只看绝对值 |
| 1.5 | 免费曲拿到整曲但 `PlayAuth` 长度 0，以为没密钥所以播不了 | 这些曲 `encrypt=null`，**本来就不加密** | `PlayAuth` 为空 ≠ 失败；只有 `encrypt=true` 才需要 `spade_a` |
| 1.6 | 12600 行的响应里找不到 `spade_a`，以为平台没下发 | 它藏在 `video_list[].encrypt_info` 里，且 `video_model` 本身是**字符串** | 先 `JSON.parse(video_model)`，再遍历 `video_list` |
| 1.7 | 队列里明明有这首，`fast=1` 却 404 | 代理读了 `recent_played_0.db`（占位库） | 目录下按文件名长度取最长的那份（带 uid 的才是真库） |

---

## 2. 设备操作层（adb / UI 自动化）

| # | 现象 | 真因 | 正确做法 |
|---|---|---|---|
| 2.1 | 🔴 点了没反应、点到了别的控件 | `adb exec-out screencap` 出来的图是 **679×1024**，实际屏是 **1200×1848** | 图片坐标 **×≈1.77**（x 1.767 / y 1.805）再 `input tap` |
| 2.2 | 🔴 搜索框输入中文变成空 | `adb shell input text` 会**整段丢弃**非 ASCII | 装 ADBKeyboard，用 `am broadcast -a ADB_INPUT_TEXT --es msg '歌名'` |
| 2.3 | `ime enable` / `ime set` 报 `Exception occurred while executing` | Android 9 上普通 shell 没权限改 IME | 必须 `adb shell "su -c 'ime set ...'"` |
| 2.4 | 🔴 输入框里没内容、也点不到「搜索」按钮 | ADBKeyboard **不弹键盘**，所以没有键盘右下角的搜索键；`keyevent 66` 也**不触发**搜索 | 点联想列表第一条，或点搜索结果条目 |
| 2.5 | 🔴 从结果页点右上角想回搜索页，结果把内容清空了 | App 停在结果页时，右上角 `(1150,70)` 是「×」不是放大镜 | 每次搜索前 `am force-stop` + `am start -n ...MainActivity` + 等 9s，保证起点唯一 |
| 2.6 | `uiautomator dump` 超时/拿不到控件树 | 播放页有动画，dump 会失败 | 别指望 UI 树；用固定坐标 + 「目标 track_id 是否出现在队列里」做校验 |
| 2.7 | Magisk 弹授权框挡住点击，流程卡住 | 首次 `su` 会弹窗 | 首次调用前先在设备上手动允许一次；脚本里对 `su` 结果做空判断 |
| 2.8 | 🔴 用户拿回平板后觉得「输入法坏了，打字不出字」 | 测试期切到了 ADBKeyboard，它不弹键盘 | 交还/关机前 `ime set` 回原输入法（本机是 `com.bytedance.android.doubaoime/.ImeService`） |
| 2.9 | 抓包后忘了撤代理，App 报网络异常 | 测试时设过 `http_proxy` | 收尾 `settings put global http_proxy :0`，并复查 |
| 2.10 | 🔴 `adb devices` 为空就以为关机了 | 重启也会短暂消失（30~60s 后回来） | 关完**等 40 秒再查一次**，仍为空才算真关机 |

---

## 3. 抓包 / hook 层

| # | 现象 | 真因 | 正确做法 |
|---|---|---|---|
| 3.1 | 🔴 切歌时抓不到任何 `/luna/` 请求，以为 hook 失效 | App 是**预取队列**，队列里有的歌直接读本地，不发请求 | 换没播过的歌，或走「搜索→点歌」强制重建会话 |
| 3.2 | `frida.enumerate_processes()` 里没有 `com.luna.music`，attach 也失败 | 有反调试，进程被隐藏 | PID 从 `adb shell ps -A | grep luna` 取，再 `dev.attach(pid)` —— 这条是通的 |
| 3.3 | 🔴 一 hook 就闪退回桌面 | 覆盖了 `CronetUrlRequest.$init`（构造器） | **不要覆盖构造器**。只 hook 方法 |
| 3.4 | 🔴 hook 后 App 卡死/崩溃 | 写成 `var o = C.start; o.implementation = function(){...; return o.call(this)}` —— `o` 就是同一个方法对象，**无限递归** | 必须用 overload 对象：`var ov = C.start.overloads[0]; ov.implementation = function(){...; return ov.call(this)}` |
| 3.5 | hook 了 `CronetEngine.newUrlRequestBuilder` 却一条都抓不到 | 字节的 Cronet 不调这个入口（实测 0 调用） | 用 `CronetUrlRequest.start`，从对象字段读 **`mInitialUrl`**（同族还有 `mFinalUrl`） |
| 3.6 | 想拿 App 真正用的取链结果，hook 网络抓不到 | 网络层是 native Cronet，Java 层 OkHttp hook 无效 | hook **JSON 解析入口**：`org.json.JSONObject.$init(String)` + `com.google.gson.Gson.fromJson`，筛含 `video_list`+`spade_a` 的字符串 |
| 3.7 | 接口路径找不到 | — | **读 APK 最快**：dex 里路径是 `..` 分隔的字符串池（`../luna/feed/song-tab../luna/activities..`），按小写段提取再剔包名，一次拿 197 条候选 |

---

## 4. 容器 / 宿主层

| # | 现象 | 真因 | 正确做法 |
|---|---|---|---|
| 4.1 | 🔴 x86 上汽水装上但一启动就崩，`UnsatisfiedLinkError: ... is for EM_AARCH64 (183) instead of EM_X86_64 (62)` | 汽水是 **arm64-only**（`secondaryCpuAbi=null`），字节 SafeMode SDK 在 `attachBaseContext` 最早期就 `System.loadLibrary` 加载 arm64 so | **x86 + 转译这条路是死的**，换 Houdini 也一样（`zygote64` `SIGSEGV / SEGV_MAPERR`）。必须上 ARM 主机 |
| 4.2 | redroid 起不来 / 报 binder 相关错误 | 内核只有 `binder` 设备文件，没有 **binderfs**（`mount -t binder` 才行） | 判据：`grep -w binder /proc/filesystems` 或 `modprobe binder_linux`。**内核 4.4 一定没有**（5.0 才进主线），4.19 看是否 Android 分支反移植 |
| 4.3 | 容器起来了但 App 被 OOM kill | 内存不够（Android 15 系统本身就吃 1.2~1.5G） | **4G 起，8G 舒服**；2G 板子别试 |
| 4.4 | 🔴 换到 redroid 后稳定报 `su: not found` | redroid 的 adbd **本身就是 root**，镜像里没有 `su` 这个二进制 | 先探 `id -u`：为 0 直接执行；否则 `su -c`；`su` 缺失再退回直跑 |
| 4.5 | 代理容器里 `adb connect 127.0.0.1:5555` 连不上 | 桥接网络看不到宿主机的 127.0.0.1 | 代理容器必须 **`network_mode: host`** |
| 4.6 | 容器里点到的位置全错 | 容器分辨率与 `config.py` 里的坐标不一致 | 起容器就用 **1200×1848 / dpi 240 / `gpu_mode=guest`**，与实测坐标对齐 |
| 4.7 | 官方文档说 `pid_max` 必须 < 65535，本机是 4194304，以为卡住了 | 实测**不是阻塞点** | 不用为它改内核参数（改它影响整机，且当前 pid 可能已超 65535） |
| 4.8 | ARM 主机上想强制指定架构 | 多架构 manifest 会自动选 | 直接 `docker pull redroid/redroid:15.0.0-latest` 即可拿到 arm64；别手写 `--platform` 反而可能选错 |
| 4.9 | 🔴 Docker Hub 拉不动 / 只有 122 KB/s | 国内直连 `registry-1.docker.io` 超时；部分镜像站限速 | 用 `docker.1ms.run/` 前缀拉（实测 7.7 MB/s，快 60 倍），拉完 `docker tag` 回原名；不改 daemon 配置 |
| 4.10 | 🔴 汽水在容器里一启动就被杀（x86 是崩溃，这里是 ANR） | `Input dispatching timed out 5003ms` —— A55 启动慢，**并发 dex2oat 时必死** | **CPU 空闲时启动**（本次 pid 稳定 70s+，界面正常）；`hide_error_dialogs=1` 挡不住 kill；`--memory 2560m` 下跑全量 AOT 会 OOM |
| 4.11 | SFTP 传 APK 报 `FileNotFoundError: [Errno 2]` | 目录是 `d---------`（root 所有），不是路径不存在 | `chmod 777` 后小文件能写，**190MB 仍会失败** → 改 SSH 管道 `cat > 文件`（25MB/s）+ 两端 sha256 校验 |
| 4.12 | adb 命令时好时坏，`device not found` | adb server 在两次命令之间被回收（进程组被杀） | 每条命令开头都 `adb connect <ip>:5555` |
| 4.13 | 点按钮没反应，坐标看着没问题 | 截图实际 1200×1848，显示端会缩放到 707 宽 | 用 PIL 找目标颜色像素质心再 tap；验证触摸是否生效可下拉通知栏看 `mCurrentFocus` |
| 4.14 | 日志有 `libnapi_v8.so not found` 就以为坏了 | APK 只带 `liblynx_v8_bridge.so`，运行时走 quickjs/LepusNG | 这是**无害警告**，别当失败 |
| 4.15 | 🔴 点「获取短信验证码」弹窗一片灰，APP 随后被杀 | 字节验证码 SDK（`BdTuringVerifyActivity`）用的是**自带 WebView**，在 redroid 的 guest 软件渲染下画不出内容 | **别在容器里登录**。把已登录设备的整包数据搬进容器（见 4.16） |
| 4.16 | 想让容器直接继承平板/手机上的登录态 | 登录态在 `databases` + `shared_prefs` + `files/keva` 里，验证码要靠 `files/turing_web_res` + `files/webview_bytedance` | 打包这 6 项（排除 `files/music`/`plugins`/`offlineX` 大缓存）→ `docker cp` → `tar xzf` → `chown -R <容器uid>` → `restorecon -R`；**uid 按容器 `dumpsys package | grep userId`**，别抄源设备的 |
| 4.17 | 加了 `webview-command-line --disable-gpu` 后 APP 直接闪退 | 字节自带 WebView 处理该开关时断言失败（`SIGTRAP`，寄存器里就是 `able-gpu` 字符串） | 这个开关对汽水**不可用**；想调 WebView 渲染这条路整体放弃 |
| 4.18 | 加了 `--in-process-gpu` 后**整个界面点击都没反应** | chromium 的输入通道被破坏，不是坐标错也不是主线程卡死 | 千万别加；只留 `--disable-gpu` 时点击正常（但验证码会崩，所以两个都别用） |
| 4.19 | `gpu_mode=swiftshader` 容器 Up、5555 在监听，但 adb 连不上（10060） | adbd 在该模式下起不来 | 直接回 `guest`，别等 |
| 4.20 | 密钥解出来 `valid=true`，但下载 403、0 字节，加 UA/Referer 都没用 | 直链自带的 `url_expire` 已过期，**不是防盗链** | 读 `video_model.url_expire`，`url_expire - now > 0` 才有效；过期就重新取链（搜索→点歌） |
| 4.21 | 🔴 App 常驻后容器整体卡死（adb 全部超时） | A55 上 App 空转 CPU 218%，`am force-stop` 报 `Broken pipe` = system_server 已崩 | **不要常驻**：`systemctl disable --now soda-keepalive`，App 按需启动；容器自愈交给 `--restart unless-stopped` |
| 4.22 | 想截 App「我的」页看畅听天数，`screencap` 卡死（40s / 0 字节） | 播放页有视频 Surface，合成器不返回；桌面和非播放页正常 | 别在播放页截图；畅听用快照文件（`benefit_state.json`）+ 标注同步时间，不假装实时 |
| 5.9 | 用容器 Cookie 调 `/luna/me` 提示「登录状态已失效」 | 需要 mssdk 签名，光有 sessionid + `x-common-params-v2` 不够 | 放弃接口取权益；账号信息改读 `account_db.login_info` |

---

## 5. 代码 / 集成层（lxserver 侧）

| # | 现象 | 真因 | 正确做法 |
|---|---|---|---|
| 5.1 | 🔴 局域网代理地址请求超时/被送到奇怪的出口 | 复用了 `musicAccount/http` 的 `request`，它会给请求挂 `getProxyAgent(url,'music')`；用户一旦配了音乐出口代理，`192.168.x.x` 也会被送去代理 | 局域网调用直连 `node:http` / `node:https`，别走统一 http 封装 |
| 5.2 | 🔴 后台预热明明成功了，用户重试还是报「只有试听」 | 代理探测写在了「仅试听 5 分钟」负缓存**之后**，被旧判定挡住 | 代理探测必须放在负缓存**之前** |
| 5.3 | 🔴 用户点一首歌要等一分半 | 把 60~90 秒的搜索兜底放进了同步链路 | 主链路只等 `fast=1`（1~7 秒）；慢路径走后台预热 + 提示「约 1 分钟后重试」 |
| 5.4 | 🔴 没配代理的用户，每首歌凭空多等 9 秒 | 每次都直接发 `/track/...` 并等超时 | 先 `isConfigured()`；再前置 `/health` 探活（60 秒缓存）；未配置/离线时**一次请求都不发** |
| 5.5 | 明明配了 `fast=1` 还是慢 | 少带了参数或参数名写错 | 契约是 `fast=1`；不带它代理会走搜索兜底（60~90 秒） |
| 5.6 | 用户连点同一首，设备被挤爆 / 互相干扰 | 代理是**单设备**，UI 操作不能并发 | 代理侧用全局串行锁；调用侧 `warmUp` 按 trackId 做 3 分钟去重 |
| 5.7 | 拿到 `spade_a` 却播不出声 | 密钥和直链**必须同档**（GetPlayInfo 是逐码率项各带一把） | 选流后按 `main_url` 回查同一档的密钥，别拿 A 档钥匙解 B 档流 |
| 5.8 | 诊断接口加了字段但前端看不到 | `/api/music/extra/diagnose` 是 **API-only**，没有前端消费者 | 别去找 app.js 的渲染处；要看就 curl |

---

## 6. 仓库 / 推送 / 发版

| # | 现象 | 真因 | 正确做法 |
|---|---|---|---|
| 6.1 | 🔴 `git fetch` 报 502，紧接着 `git reset --hard origin/main` 把本地新提交抹掉了 | fetch 失败时 `origin/main` 还是**旧 ref**，reset 就回退到旧版 | **reset 前先确认 fetch 成功**；失败时改用远端 Contents API 逐个下载恢复 |
| 6.2 | 空仓库用 Git Data API 建 blob 报 `409 Git Repository is empty` | Data API 不能对空仓库操作 | 先用 **Contents API** 建首个提交（打开 main 分支），之后才能用 Data API |
| 6.3 | 🔴 更新已有文件报 `422 "sha" wasn't supplied` | Contents API 改文件必须带 `sha` | 先 `GET /contents/<path>` 拿 sha 再 PUT |
| 6.4 | 🔴 新建文件报 `404 Not Found` | 反过来：**远端不存在的文件不能带 sha** | 先 GET；404 就按「新建」PUT（不带 sha），其余带 sha |
| 6.5 | 容器里 bash 报 `bad interpreter: No such file or directory` | `.sh` 被 checkout 成 CRLF | `.gitattributes` 写 `*.sh text eol=lf`；提交前确认 blob 里没有 `\r` |
| 6.6 | `git push` 卡死 / 502 | 本机到 github.com 直连不通 | 走 `C:/tmp/gh_push2.py`（Git Data API）或 Contents API；网页侧走 7890 代理 |
| 6.7 | 远端 commit 历史里一堆同名提交 | Contents API 是**一个文件一个 commit** | 正常现象，不用清理；但要知道 `git log` 会重复 |
| 6.8 | 发版后镜像里版本号没变 | `release.yml` 读的是 `public/js/config.js` 的 version | 发版前两处版本号必须一致，CI 还会校验 tag == package.json |

---

## 7. 相邻项目的误操作（本项目经常连着 lxserver 一起动）

这几条不在本仓库代码里，但**在这个项目里动 246 就会碰到**，一并记下：

| # | 误操作 | 后果 | 正确做法 |
|---|---|---|---|
| 7.1 | 用 `docker rm` + `docker run` 换版本 | 丢掉 compose 项目标签，容器脱离编排 | `file rename` 备份 yml → `file upload --overwrite replace` → `composeBuild` |
| 7.2 | 换容器时沿用旧的 `logs` 目录 | 上一版镜像的历史日志混进来，首轮验收全误判 | 每轮**单独新建** `logs-test-<tag>`，并同步 harness 的 `SOAK_LOGDIR` |
| 7.3 | 测试进行中重启容器修日志目录 | 重置 uptime 基准、打断 90 秒周期 | 测试期间不动容器；要改就下一轮 |
| 7.4 | `docker exec grep '中文'` 验证构建产物 | `sh -c` 会把中文参数搞坏，结果不可信 | 一律用 ASCII 标识符（函数名/常量名）验证 |
| 7.5 | 拿 `/status.positionMs` 当歌曲内进度 | 那是**流内位置**，曲内 = `+seekOffsetMs` | 判进度要加偏移量 |

---

## 8. 动手前 / 交付前自查

**动手前**

- [ ] 手册跑过 `precheck.py <域>`，不是只 `search`
- [ ] 读的是哪张表 / 哪个库，确认过「这张表在这个场景会不会写」
- [ ] 屏幕分辨率与坐标换算确认过（截图尺寸 ≠ 实际分辨率）
- [ ] 设备上是 root 还是 adbd 已 root（决定走不走 `su`）

**改动代码时**

- [ ] 未配置时是否**零请求**（别给没配的人加延迟）
- [ ] 慢操作是否已后台化 / 是否进了同步链路
- [ ] 负缓存 / 短路判断有没有挡住「后来修好」的路径
- [ ] 局域网请求有没有走统一 http 封装（会被出口代理带偏）

**交付前**

- [ ] 输入法、代理、hook、测试容器都收干净
- [ ] 新增的 `.sh` 确认是 LF
- [ ] 公开仓库扫描过凭据（`grep -E "password|token|Bearer|192\.168\.[0-9]+\.[0-9]+"`）
- [ ] 结论里有数字/报错原文，没有「理论上可行」这种话
