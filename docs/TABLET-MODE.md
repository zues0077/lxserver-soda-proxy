# 平板模式操作要点（已验证 5/5 通过）

> 适用：把一台**已 root、已登录汽水**的安卓平板当作「取链设备」，soda-proxy 通过 adb 操作它。
> 这条路线在 2026-11-06 完成过 5 首歌的压测（花鸦 / 夜曲 / 没那种命 / 晴天 / 稻香），
> 端到端解密 AAC 首元素 100% 合法。**是目前唯一被证明「稳定可用」的取链方式。**

## 一、前置条件（缺一不可）

| 项 | 要求 | 怎么确认 |
|---|---|---|
| root | Magisk 等，`su -c` 可用 | `adb shell "su -c 'id -u'"` → `0` |
| 汽水已登录 | 同一个号，且已绑定畅听/会员 | 打开 App「我的」能看到昵称 |
| 屏幕常亮 | 否则熄屏后点击全落空 | `adb shell "svc power stayon true"` |
| 分辨率固定 | 1200×1848（坐标按这个标定） | `adb shell wm size` |
| 输入法 | ADBKeyboard（中文输入必需） | `adb shell settings get secure default_input_method` |

## 二、接入方式（两种，任选）

### A. USB 连接（最稳，本机直连）
```bash
adb devices                      # 应看到 0123456789ABCDEF  device
adb -s <serial> shell getprop ro.product.model
```

### B. 无线 adb（NAS 侧取链用这个）
```bash
# 平板先用 USB 连一次电脑，然后：
adb -s <serial> tcpip 5555
adb shell ip -f inet addr show wlan0 | grep 'inet '     # 拿到内网 IP，例如 192.168.1.177

# 在 NAS 上（soda-proxy 所在机器）：
adb connect <平板IP>:5555
# 首次会弹「允许 USB 调试吗」，必须在平板上点「允许」（勾"一律允许"）
```
⚠️ **这一步必须在平板旁边操作**。点不了就等于没授权，`adb connect` 会一直 `unauthorized`。

## 三、取链流程（soda-proxy 已封装，这是原理）

```
搜索歌名 → 点第一条联想 → 进入搜索结果页 → 点目标条目 → App 播放并取链
                                                        ↓
                                    cached_playable 表写入 video_model
                                                        ↓
                                读 recent_played_<uid>.db 拿 main_url + spade_a
```

### 三个必须（踩过的坑）
1. **搜索前必须 force-stop 冷启动**
   App 停在结果页时右上角 `(1150,70)` 是「×」不是搜索图标，点下去只清空、不跳转。
   正确顺序：`am force-stop` → `am start -n com.luna.music/com.luna.biz.main.main.MainActivity` → 等 9 秒（落在发现页）。
2. **歌名必须去括号**
   搜「稻香（杰伦）」出不来，搜「稻香」才有。`soda_proxy/resolver.py` 的 `clean_name()` 已处理。
3. **必须等 `video_list` 写完**
   App 是先写 `cached_playable` 记录、**异步**补 `video_model`。只判断 id 存在会拿到半成品。

## 四、坐标表（1200×1848 / dpi240）

| 目标 | 坐标 |
|---|---|
| 右上角搜索图标（冷启动后） | 1150, 70 |
| 搜索输入框 | 400, 82 |
| 联想第一条 | 220, 139 |
| 搜索结果 x（条目横坐标） | 220 |
| 结果条目 y（有推荐卡片） | 473 / 592 / 711 |
| 结果条目 y（无卡片） | 353 / 472 / 591 |
| 底部「我的」 | 1079, 1727 |
| 「手机号登录」 | 596, 1704 |
| 协议勾选框 | 306, 490 |
| 手机号输入框 | 509, 390 |
| 「获取短信验证码」 | 596, 594 |
| 隐私「同意」（绿色按钮） | 599, 1185 |

## 五、中文输入（`input text` 会丢中文）

```bash
# 1. 装 ADBKeyboard（17KB）
adb install -r ADBKeyboard.apk
# 2. 启用（Android 9 上必须 su）
adb shell "su -c 'ime enable com.android.adbkeyboard/.AdbIME'"
adb shell "su -c 'ime set com.android.adbkeyboard/.AdbIME'"
# 3. 输入
adb shell am broadcast -a ADB_INPUT_TEXT --es msg '花鸦'
# 4. 用完切回原输入法
adb shell "su -c 'ime set com.bytedance.android.doubaoime/.ImeService'"
```
ADBKeyboard **不弹键盘**，所以没有「搜索」按钮可点 —— 用 `keyevent 66` 或直接点联想项。

## 六、读库取链

```bash
# 库位置
/data/data/com.luna.music/databases/recent_played_<uid>.db   → cached_playable(id, queueId, playableJson)
# 关键：playableJson.track.player_info.video_model 是 **JSON 字符串**，要二次 parse
# 里面才有 main_url + encrypt_info.spade_a
```
⚠️ **不是** `player_<uid>.db` 的 `player_info` 表 —— 那个只在队列预取时写，单曲播放完全不写。

## 七、直链有效期（重要）

`video_model` 顶层带 `url_expire`（秒级时间戳），**约 2 小时**。过期后下载 **403**，
加 UA / Referer 全都无效（不是防盗链，是链接本身死了）。

判定：`url_expire - now > 120` 才算新鲜。soda-proxy 已内置这个判断，过期会返回
`{ok:false, stale:true, urlExpiresIn:-N}` 而不是死链。

## 八、关机 / 交还前必做

```bash
adb shell "su -c 'ime set com.bytedance.android.doubaoime/.ImeService'"   # 输入法切回
adb shell settings get global http_proxy                                  # 应为 :0
adb shell "su -c 'reboot -p'"                                             # 真关机（reboot 是重启）
# 40 秒后再查一次 adb devices，仍为空才算关机成功
```

## 九、实测性能（平板 vs RK3566 容器）

| 项 | 平板（骁龙/麒麟，已验证） | RK3566 容器 |
|---|---|---|
| 队列命中 | **1.0~1.2 秒** | 1.0 秒 |
| 搜索兜底 | **59~70 秒** | 126~148 秒且**点不中**，CPU 282% 拖垮系统 |
| 稳定性 | 5/5 通过 | App 一启动就卡满 |

→ **结论：平板能取链，RK3566 只能读缓存。**
