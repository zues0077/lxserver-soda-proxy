#!/usr/bin/env python3
"""汽水容器 · 账号面板（只依赖标准库 + adb + docker）。

定位：**给用户看的**只有一个账号面板 —— 登录方式、账号状态、容器保活、畅听权益。
原始手机屏幕属于「高级/排障」，默认折叠，不占主界面。

数据来源（都在容器内本地读，不碰签名、不调需要 msskc 的接口）：
  昵称/UID/头像 → /data/data/com.luna.music/databases/account_db 的 login_info 表
  畅听权益      → 导航到 App「我的」页截图，前端按坐标裁出那一行（不 OCR，图即事实）
  容器保活      → docker inspect + systemd soda-keepalive + adb pidof

用法：
    python3 webui.py --serial 127.0.0.1:5555 --port 18091 --container soda-android13

接口：
    GET  /                 账号面板
    GET  /api/summary      账号 + 容器 + App 状态（JSON）
    GET  /api/benefit      截图并返回「我的」页 PNG（前端裁剪显示畅听栏）
    GET  /api/screen       原始屏幕 PNG（高级用）
    GET  /tap?x=&y=        点击
    GET  /text?s=          输入
    GET  /key?k=4          按键
    GET  /launch           启动汽水
    GET  /health           健康检查

注意：**没有鉴权**，只应监听内网。
"""
import argparse
import json
import os
import subprocess
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {
    'serial': '127.0.0.1:5555',
    'adb': 'adb',
    'docker': 'docker',
    'container': 'soda-android13',
    'keepalive_unit': 'soda-keepalive',
    'last_shot': None,
    'last_shot_at': 0,
    'account_cache': None,
    'account_at': 0,
    'benefit_png': None,
    'benefit_at': 0,
}
_LOCK = threading.Lock()

PKG = 'com.luna.music'
ACT = PKG + '/com.luna.biz.main.main.MainActivity'
# 「我的」页里「免费听·N天」那一栏在 1200x1848 截图中的位置（前端按此裁剪）
BENEFIT_BOX = {'x': 20, 'y': 640, 'w': 1160, 'h': 170}


def adb(*args, timeout=30):
    cmd = [STATE['adb']]
    if STATE['serial']:
        cmd += ['-s', STATE['serial']]
    cmd += list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode, (r.stdout or b''), (r.stderr or b'')
    except subprocess.TimeoutExpired:
        return 124, b'', b'timeout'


def docker(*args, timeout=30):
    """返回 (code, stdout_bytes, stderr_text)。stdout 保持 bytes —— 读 sqlite 文件时不能过 decode。"""
    try:
        r = subprocess.run([STATE['docker']] + list(args), capture_output=True, timeout=timeout)
        return r.returncode, (r.stdout or b''), (r.stderr or b'').decode('utf-8', 'replace')
    except Exception as e:
        return 1, b'', str(e)


def ensure_device():
    """确保 adb server 起来、设备已连接。返回 (ok, message)。"""
    adb('start-server', timeout=20)
    code, out, err = adb('get-state', timeout=15)
    if out.decode('utf-8', 'replace').strip() == 'device':
        return True, 'device'
    adb('connect', STATE['serial'], timeout=15)
    time.sleep(1.5)
    code, out, err = adb('get-state', timeout=15)
    state = out.decode('utf-8', 'replace').strip()
    return (state == 'device'), (state or err.decode('utf-8', 'replace').strip())


def screenshot():
    code, out, err = adb('exec-out', 'screencap', '-p', timeout=40)
    if code == 0 and out[:4] == b'\x89PNG':
        STATE['last_shot'] = out
        STATE['last_shot_at'] = time.time()
        return out
    return None


def app_pid():
    code, out, _ = adb('shell', 'pidof', PKG, timeout=15)
    return out.decode('utf-8', 'replace').strip()


def foreground():
    code, out, _ = adb('shell', 'dumpsys', 'activity', 'activities', timeout=25)
    for line in out.decode('utf-8', 'replace').splitlines():
        if 'topResumedActivity' in line:
            return line.strip()
    return ''


def read_account():
    """从容器里的 account_db.login_info 读账号。带 60 秒缓存。"""
    now = time.time()
    if STATE['account_cache'] and now - STATE['account_at'] < 60:
        return STATE['account_cache']

    info = {'loggedIn': False, 'name': '', 'uid': '', 'avatar': '', 'source': 'container'}
    tmp = '/tmp/_soda_account.db'
    code, out, err = docker('exec', STATE['container'], 'cat',
                           '/data/data/com.luna.music/databases/account_db', timeout=40)
    if code != 0:
        return info
    try:
        import sqlite3
        with open(tmp, 'wb') as f:
            f.write(out)
        c = sqlite3.connect(tmp)
        cur = c.cursor()
        cur.execute('SELECT uid, screen_name, avatar_url, time FROM login_info LIMIT 1')
        row = cur.fetchone()
        c.close()
        if row:
            info.update({
                'loggedIn': bool(row[0]),
                'name': row[1] or '',
                'uid': str(row[0] or ''),
                'avatar': row[2] or '',
                'loginAt': row[3] or 0,
            })
    except Exception as e:
        info['error'] = str(e)[:120]
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    STATE['account_cache'] = info
    STATE['account_at'] = now
    return info


def container_status():
    code, out, _ = docker('ps', '--filter', 'name=' + STATE['container'],
                          '--format', '{{.Status}}', timeout=20)
    status = out.decode('utf-8', 'replace').strip() if isinstance(out, bytes) else str(out).strip()
    running = status.lower().startswith('up')

    policy = ''
    try:
        c2, o2, _ = docker('inspect', '--format', '{{.HostConfig.RestartPolicy.Name}}',
                           STATE['container'], timeout=20)
        policy = o2.decode('utf-8', 'replace').strip() if isinstance(o2, bytes) else ''
    except Exception:
        pass

    return {
        'container': STATE['container'],
        'status': status or 'not found',
        'running': running,
        # 容器级自愈：docker 的 restart policy（App 不常驻，避免 A55 上 CPU 空转）
        'autorestart': policy or 'no',
        'keepalive': policy or 'no',
    }


BENEFIT_CACHE = '/tmp/_soda_benefit.png'
BENEFIT_STATE = '/tmp/_soda_benefit.json'


def read_benefit_state():
    """畅听权益快照。容器内实时截图不可行（App 播放时 screencap 卡死），
    所以这里存「最近一次在 App 内确认的值 + 时间」，不猜、不 OCR。"""
    try:
        with open(BENEFIT_STATE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'days': None, 'stage': '', 'syncedAt': '', 'note': '尚未同步'}


def write_benefit_state(obj):
    try:
        with open(BENEFIT_STATE, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)
    except OSError:
        pass


def adb_alive():
    """快速探活（5 秒）。容器内 system_server 崩掉时 adb 会整体卡住，先挡掉。"""
    code, out, _ = adb('shell', 'echo', 'ok', timeout=6)
    return code == 0 and b'ok' in out


def benefit_shot(force=False):
    """导航到「我的」页并截图（给前端裁剪出畅听那一栏）。

    注意：汽水播放页带视频 Surface 时 `screencap` 会卡死（实测 40s 无返回），
    所以这里全程短超时，失败就回上次缓存的图，绝不让请求挂住。
    """
    now = time.time()
    if not force and STATE['benefit_png'] and now - STATE['benefit_at'] < 30:
        return STATE['benefit_png']

    if not adb_alive():
        return STATE['benefit_png']

    pid = app_pid()
    if not pid:
        adb('shell', 'am', 'start', '-n', ACT, timeout=25)
        time.sleep(22)          # A55 上首屏要 20 秒上下
    elif 'MainActivity' not in foreground():
        adb('shell', 'am', 'start', '-n', ACT, timeout=25)
        time.sleep(5)

    # 底部导航「我的」→ 等页面稳定 → 截图（短超时）
    adb('shell', 'input', 'tap', '1006', '1724', timeout=15)
    time.sleep(3)

    code, out, _ = adb('exec-out', 'screencap', '-p', timeout=20)
    if code == 0 and out[:4] == b'\x89PNG':
        STATE['benefit_png'] = out
        STATE['benefit_at'] = time.time()
        try:
            with open(BENEFIT_CACHE, 'wb') as f:
                f.write(out)
        except OSError:
            pass
        return out

    # 截图失败：退回磁盘上的上次快照（服务重启后仍可用）
    if STATE['benefit_png']:
        return STATE['benefit_png']
    try:
        with open(BENEFIT_CACHE, 'rb') as f:
            STATE['benefit_png'] = f.read()
            return STATE['benefit_png']
    except OSError:
        return None


PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>汽水账号面板</title>
<style>
:root{color-scheme:dark;--bg:#0f1115;--card:#171a21;--line:#262b36;--txt:#e6e8ee;--dim:#8b93a7;--ok:#22c55e;--warn:#f59e0b;--bad:#ef4444;--brand:#4f8cff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:24px 20px 60px}
h1{font-size:19px;margin:0 0 4px;font-weight:600}
.sub{color:var(--dim);font-size:12px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:760px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.card h2{font-size:13px;margin:0 0 12px;color:var(--dim);font-weight:500;letter-spacing:.4px}
.acct{display:flex;gap:14px;align-items:center}
.av{width:56px;height:56px;border-radius:50%;object-fit:cover;background:#242a35;flex:0 0 auto}
.nm{font-size:17px;font-weight:600}
.uid{color:var(--dim);font-size:12px;margin-top:2px}
.pill{display:inline-flex;align-items:center;gap:5px;font-size:12px;padding:2px 9px;border-radius:999px;background:#1e2530;color:var(--dim)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--dim);display:inline-block}
.dot.ok{background:var(--ok)} .dot.warn{background:var(--warn)} .dot.bad{background:var(--bad)}
.rows{display:flex;flex-direction:column;gap:9px;margin-top:4px}
.row{display:flex;justify-content:space-between;font-size:13px}
.row span:first-child{color:var(--dim)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.benefit{margin-top:4px;border-radius:10px;overflow:hidden;background:#101318;border:1px solid var(--line);position:relative;height:110px}
.benefit .shot{position:absolute;left:0;top:0;transform-origin:0 0}
.benefit .ph{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--dim);font-size:12px}
button{background:#232a36;color:var(--txt);border:1px solid #333c4c;border-radius:8px;padding:9px 13px;font-size:13px;cursor:pointer;font-family:inherit}
button:hover{background:#2b3342}
button:disabled{opacity:.45;cursor:not-allowed}
button.primary{background:var(--brand);border-color:var(--brand);color:#fff}
button.primary:hover{filter:brightness(1.08)}
.btns{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.loginway{display:flex;gap:10px;margin-top:10px}
.loginway button{flex:1;padding:14px 10px;text-align:center;line-height:1.4}
.loginway .t{font-weight:600;display:block;font-size:14px}
.loginway .d{font-size:11px;color:var(--dim);display:block;margin-top:3px}
.note{font-size:12px;color:var(--dim);margin-top:10px;line-height:1.65}
.note b{color:#c9d1e0;font-weight:600}
details{margin-top:22px;border:1px solid var(--line);border-radius:12px;background:var(--card);overflow:hidden}
summary{cursor:pointer;padding:13px 18px;font-size:13px;color:var(--dim);user-select:none}
summary:hover{color:var(--txt)}
.adv{padding:0 18px 18px}
.loginform{display:grid;grid-template-columns:1fr auto;gap:8px;margin:4px 0 10px}
.loginform input{background:#14161c;border:1px solid var(--line);color:#e8eaf0;border-radius:8px;
  padding:10px 12px;font-size:14px;outline:none}
.loginform input:focus{border-color:#4c7dff}
.loginform button.primary{background:#2563eb;color:#fff;border:none;border-radius:8px;
  padding:10px 16px;font-size:14px;cursor:pointer}
.loginform button.primary:active{opacity:.85}
#scr{width:100%;max-width:360px;border-radius:8px;border:1px solid var(--line);display:block;cursor:crosshair;
  -webkit-user-drag:none;user-select:none;touch-action:none}
.tag{font-size:11px;padding:1px 7px;border-radius:999px;background:#1e2530;color:var(--dim);margin-left:6px}
#toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:#232a36;border:1px solid #3a4557;padding:10px 18px;border-radius:10px;font-size:13px;opacity:0;pointer-events:none;transition:.25s}
#toast.on{opacity:1}
</style></head>
<body>
<div class="wrap">
  <h1>汽水账号</h1>
  <div class="sub">登录状态、畅听权益与容器保活一屏看完；原始屏幕在页面底部「高级」里</div>

  <div class="grid">
    <!-- 账号 -->
    <div class="card">
      <h2>账号</h2>
      <div class="acct">
        <img class="av" id="av" alt="">
        <div>
          <div class="nm" id="nm">读取中…</div>
          <div class="uid mono" id="uid"></div>
          <div style="margin-top:6px"><span class="pill" id="loginPill"><i class="dot"></i><span>检测中</span></span></div>
        </div>
      </div>
    </div>

    <!-- 容器 -->
    <div class="card">
      <h2>运行状态</h2>
      <div class="rows">
        <div class="row"><span>容器</span><span id="cContainer" class="mono">—</span></div>
        <div class="row"><span>汽水进程</span><span id="cPid" class="mono">—</span></div>
        <div class="row"><span>容器自愈</span><span id="cKeepalive">—</span></div>
        <div class="row"><span>更新时间</span><span id="cTime" class="mono">—</span></div>
      </div>
    </div>

    <!-- 畅听 -->
    <div class="card">
      <h2>畅听权益</h2>
      <div style="display:flex;align-items:baseline;gap:9px">
        <div style="font-size:40px;font-weight:700;line-height:1.05" id="bfDays">—</div>
        <div style="color:var(--dim);font-size:13px">天畅听</div>
      </div>
      <div class="rows" style="margin-top:12px">
        <div class="row"><span>任务进度</span><span id="bfStage">—</span></div>
        <div class="row"><span>最后同步</span><span id="bfTime" class="mono">—</span></div>
      </div>
      <div class="btns">
        <button onclick="loadBenefit(true)">刷新</button>
        <button onclick="openApp()">打开 App 查看</button>
      </div>
      <div class="note" id="bfNote"></div>
    </div>

    <!-- 登录 -->
    <div class="card">
      <h2>登录方式</h2>
      <div class="loginform">
        <input id="phone" type="tel" inputmode="numeric" maxlength="11" placeholder="手机号（11 位）">
        <button class="primary" onclick="sendCode()">获取验证码</button>
        <input id="code" type="tel" inputmode="numeric" maxlength="6" placeholder="短信验证码">
        <button class="primary" onclick="doLogin()">登录</button>
      </div>
      <div class="note" id="loginNote">
        当前账号已登录，无需再次登录。换号时才需要走下面的流程。
      </div>
      <div class="note" style="color:#8b93a7">
        说明：这版汽水<b>没有扫码登录</b>——它的登录页只有「手机号 + 短信验证码」，
        没有二维码、也没有抖音授权按钮（实测截图确认）。所以这里只提供手机号登录。<br>
        点「获取验证码」后是<b>全自动</b>的：容器里会自动打开登录页、填号码、勾协议、
        点获取验证码、自动过滑块，约 30~60 秒。你只需要把收到的验证码填到下面第二格点登录。
      </div>
    </div>

    <!-- 畅听任务 -->
    <div class="card" style="grid-column:1/-1">
      <h2>畅听任务 <span class="tag">后续开放</span></h2>
      <div class="rows">
        <div class="row"><span>每日领取</span><span>App「我的」页有「免费听·N天 / 领畅听」，每日可领 1 天</span></div>
        <div class="row"><span>自动执行</span><span>待接入（需先验证按钮可稳定命中，避免误点）</span></div>
      </div>
      <div class="note">这块是规划位。做之前我会先实测领取按钮的命中率，不拿你的账号做盲试。</div>
    </div>
  </div>

  <details>
    <summary>高级 · 排障（原始屏幕与手动操作）</summary>
    <div class="adv">
      <div class="note" style="margin:0 0 12px">
        这里是容器的<b>真实屏幕</b>，只在排障时需要。日常不用看它。
      </div>
      <img id="scr" alt="屏幕" draggable="false">
      <div class="btns">
        <button onclick="refreshScreen()">刷新画面</button>
        <button onclick="key(4)">返回</button>
        <button onclick="key(3)">主页</button>
        <button onclick="key(66)">回车</button>
      </div>
      <div class="btns">
        <input id="txt" placeholder="输入内容（数字/英文）" style="flex:1;background:#1b2029;border:1px solid #333c4c;color:var(--txt);border-radius:8px;padding:9px 12px;font-family:inherit">
        <button onclick="sendText()">输入</button>
      </div>
      <div class="note" id="advStatus"></div>
    </div>
  </details>
</div>
<div id="toast"></div>

<script>
const $ = id => document.getElementById(id);
let toastTimer = null;
function toast(t){ const e=$('toast'); e.textContent=t; e.classList.add('on'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>e.classList.remove('on'),2600); }

function dotClass(v){ return v==='ok'?'ok':(v==='warn'?'warn':'bad'); }

async function loadSummary(){
  try{
    const r = await fetch('/api/summary?t='+Date.now());
    const d = await r.json();
    const a = d.account || {}, c = d.container || {}, app = d.app || {};

    $('nm').textContent = a.loggedIn ? (a.name || '已登录') : '未登录';
    $('uid').textContent = a.loggedIn && a.uid ? ('UID ' + a.uid) : '';
    if (a.avatar) { $('av').src = a.avatar; $('av').style.display=''; }
    else { $('av').style.display='none'; }

    const pill = $('loginPill');
    pill.innerHTML = '<i class="dot ' + (a.loggedIn?'ok':'bad') + '"></i><span>' + (a.loggedIn?'已登录':'未登录') + '</span>';

    $('cContainer').textContent = c.status || '未知';
    $('cPid').textContent = app.pid ? ('PID ' + app.pid + ' · 运行中') : '未运行';
    const ar = !!c.autorestart && c.autorestart !== 'no';
    $('cKeepalive').innerHTML = '<i class="dot ' + (ar?'ok':'warn') + '"></i> ' + (ar ? ('已开启（' + c.autorestart + '）') : '未开启');
    $('cTime').textContent = new Date().toLocaleTimeString();

    if (a.loggedIn) {
      $('loginNote').innerHTML = '当前账号已登录，无需再次登录。换号时才需要走下面的流程。';
    }
  }catch(e){ toast('状态读取失败：'+e); }
}

async function loadBenefit(force){
  try{
    const r = await fetch('/api/benefit?t=' + Date.now());
    const d = await r.json();
    $('bfDays').textContent = (d.days == null) ? '—' : d.days;
    $('bfStage').textContent = d.stage || '每日可领 1 天';
    $('bfTime').textContent = d.syncedAt || '尚未同步';
    $('bfNote').innerHTML = d.note || '';
    if (force) toast('已刷新');
  }catch(e){
    $('bfNote').textContent = '读取失败：' + e;
  }
}

function loginNote(msg, bad){
  const n = $('loginNote');
  if(!n) return;
  n.textContent = msg;
  n.style.color = bad ? '#f56c6c' : '';
}
function sendCode(){
  const p = ($('phone').value || '').trim();
  if(!/^1\d{10}$/.test(p)){ toast('手机号要 11 位'); loginNote('手机号要 11 位', true); return; }
  loginNote('正在打开登录页、填手机号、点获取验证码，再自动过滑块…（约 30 秒）');
  fetch('/api/login/send?phone=' + encodeURIComponent(p))
    .then(r => r.json())
    .then(d => {
      const steps = (d.steps || []).join(' → ');
      loginNote((d.ok ? '✅ 验证码已发送：' : '❌ 没通过：') + steps, !d.ok);
      if(d.ok) { toast('已发送，去收短信'); }
      else { toast('滑块没过，点「获取验证码」重试一次'); }
      refreshScreen();
    })
    .catch(e => loginNote('调用失败：' + e, true));
}
function doLogin(){
  const c = ($('code').value || '').trim();
  if(!/^\d{4,6}$/.test(c)){ toast('验证码是 4~6 位数字'); loginNote('验证码是 4~6 位数字', true); return; }
  loginNote('正在提交验证码…');
  fetch('/api/login/code?code=' + encodeURIComponent(c))
    .then(r => r.json())
    .then(d => {
      loginNote(d.ok ? '✅ 已提交，正在校验…' : '❌ ' + (d.error || '失败'), !d.ok);
      setTimeout(loadSummary, 3000);
      refreshScreen();
    })
    .catch(e => loginNote('调用失败：' + e, true));
}
function showLogin(kind){
  const n = $('loginNote');
  if (kind === 'qr') {
    toast('这一版汽水没有扫码入口（只有手机号验证码）');
    if (n) n.textContent = '这一版汽水没有扫码入口，只能用手机号 + 短信验证码登录。';
    return;
  }
  // 反馈要看得见：toast 2.6 秒就消失，画面又在页面底部，
  // 所以这里同时写 loginNote 并自动滚到远程画面。
  if (n) n.textContent = '正在把 App 切到登录页…';
  fetch('/login/open?t='+Date.now()).then(r=>r.text()).then(t=>{
    toast(t);
    if (n) n.textContent = t;
    setTimeout(refreshScreen, 800);
    const s = $('scr');
    if (s) s.scrollIntoView({behavior:'smooth', block:'center'});
  }).catch(e=>{ if (n) n.textContent = '调用失败：' + e; });
}

function openApp(){ fetch('/launch').then(r=>r.text()).then(t=>toast(t)); }

function refreshScreen(){
  const i = new Image();
  i.onload = () => { $('scr').src = i.src; $('advStatus').textContent = '画面 ' + new Date().toLocaleTimeString(); };
  i.onerror = () => { $('advStatus').textContent = '截图失败'; };
  i.src = '/shot?t=' + Date.now();
}
function key(k){ fetch('/key?k='+k).then(r=>r.text()).then(t=>{ $('advStatus').textContent=t; setTimeout(refreshScreen,600); }); }
function sendText(){
  const v = $('txt').value; if(!v) return;
  fetch('/text?s='+encodeURIComponent(v)).then(r=>r.text()).then(t=>{ $('advStatus').textContent=t; $('txt').value=''; });
}
// 画面坐标换算：图片显示尺寸 -> 设备像素
function toDev(ev){
  const im = $('scr'), r = im.getBoundingClientRect();
  return [Math.round((ev.clientX-r.left)*im.naturalWidth/r.width),
          Math.round((ev.clientY-r.top)*im.naturalHeight/r.height)];
}
let dragFrom=null, dragged=false;
$('scr').addEventListener('dragstart', e=>e.preventDefault());
$('scr').addEventListener('pointerdown', e=>{
  e.preventDefault();
  try{ $('scr').setPointerCapture(e.pointerId); }catch(_){}
  dragFrom = toDev(e); dragged = false;
  $('advStatus').textContent = '按下 '+dragFrom[0]+','+dragFrom[1]+'，拖动中…';
});
$('scr').addEventListener('pointermove', e=>{
  if(!dragFrom) return;
  e.preventDefault();
  const p = toDev(e);
  if(Math.abs(p[0]-dragFrom[0])>6 || Math.abs(p[1]-dragFrom[1])>6){
    dragged = true;
    $('advStatus').textContent = '拖动 '+dragFrom[0]+','+dragFrom[1]+' → '+p[0]+','+p[1];
  }
});
$('scr').addEventListener('pointerup', e=>{
  if(!dragFrom) return;
  e.preventDefault();
  const from = dragFrom, to = toDev(e); dragFrom = null;
  if(!dragged){ $('advStatus').textContent = '点击 '+to[0]+','+to[1]; return; }
  $('advStatus').textContent = '松手，发送拖动…';
  fetch('/swipe?x1='+from[0]+'&y1='+from[1]+'&x2='+to[0]+'&y2='+to[1])
    .then(r=>r.text()).then(t=>{ $('advStatus').textContent=t; setTimeout(refreshScreen, 900); })
    .catch(err=>{ $('advStatus').textContent='拖动失败: '+err; });
});
$('scr').addEventListener('click', e=>{
  if(dragged){ dragged=false; return; }      // 拖完的那一下不当作点击
  const p = toDev(e);
  fetch('/tap?x='+p[0]+'&y='+p[1]).then(r=>r.text()).then(()=>{ $('advStatus').textContent='点击 '+p[0]+','+p[1]; setTimeout(refreshScreen,500); });
});

loadSummary();
setInterval(loadSummary, 10000);
loadBenefit(false);
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = 'soda-webui/0.2'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype='text/plain; charset=utf-8'):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _proxy_proxy(self, path):
        """把请求转给同机的取链代理（18090），它才有 captcha 识别与拖动逻辑。

        两个容器是同镜像的不同服务，面板（18091）只负责界面。
        """
        import json as _json
        url = 'http://host.docker.internal:18090' + path
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                body = r.read().decode('utf-8', 'replace')
                code = r.status
        except urllib.error.HTTPError as e:
            # 代理用 4xx 表示「滑块没通过」，body 里是详细 steps，必须透传出去，
            # 否则面板只能显示一句 "HTTP Error 409"，看不出卡在哪一步。
            body = e.read().decode('utf-8', 'replace')
            code = e.code
        except Exception as e:
            return self._json({'ok': False, 'error': '%s: %s' % (type(e).__name__, e)})
        self.send_response(code)
        payload = body.encode('utf-8')
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, obj):
        return self._send(200, json.dumps(obj, ensure_ascii=False), 'application/json; charset=utf-8')

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path

        if p == '/':
            return self._send(200, PAGE, 'text/html; charset=utf-8')

        if p == '/api/summary':
            ok, dev = ensure_device()
            return self._json({
                'device': {'ok': ok, 'state': dev, 'serial': STATE['serial']},
                'account': read_account(),
                'container': container_status(),
                'app': {'pid': app_pid() if ok else '', 'foreground': foreground() if ok else ''},
                'benefitBox': BENEFIT_BOX,
            })

        if p == '/api/benefit':
            st = read_benefit_state()
            # 顺带报 App 是否在跑（读取权益需要唤醒 App，但容器内截图不可靠，故只做提示）
            st['appRunning'] = bool(app_pid()) if ensure_device()[0] else False
            return self._json(st)

        if p == '/api/benefit/set':
            days = q.get('days', [''])[0]
            stage = q.get('stage', [''])[0]
            note = q.get('note', [''])[0]
            cur = read_benefit_state()
            if days:
                try:
                    cur['days'] = int(days)
                except ValueError:
                    pass
            if stage:
                cur['stage'] = stage
            if note:
                cur['note'] = note
            cur['syncedAt'] = time.strftime('%Y-%m-%d %H:%M')
            write_benefit_state(cur)
            return self._json(cur)

        if p == '/api/screen':
            with _LOCK:
                png = screenshot()
            if png:
                return self._send(200, png, 'image/png')
            return self._send(503, 'no screenshot')

        if p == '/api/login/send':
            phone = (q.get('phone') or [''])[0].strip()
            return self._proxy_proxy('/login/send?phone=' + urllib.parse.quote(phone))

        if p == '/api/login/code':
            code = (q.get('code') or [''])[0].strip()
            return self._proxy_proxy('/login/code?code=' + urllib.parse.quote(code))

        if p == '/login/open':
            # 把 App 切到登录页：热启动 + 点底部「我的」（未登录时「我的」就是登录入口）
            adb('shell', 'am', 'start', '-n', ACT, timeout=40)
            time.sleep(1.5)
            adb('shell', 'input', 'tap', '1000', '1734', timeout=20)
            time.sleep(1.5)
            return self._send(200, '已切到登录页：在下方画面点手机号框，用「输入文字」填号码，勾选协议后点「获取短信验证码」')

        if p == '/launch':
            adb('shell', 'am', 'start', '-n', ACT, timeout=40)
            time.sleep(1.0)
            pid = app_pid()
            return self._send(200, '已启动汽水（pid=%s）' % (pid or '启动中'))

        if p == '/status':
            return self._json({'pid': app_pid(), 'top': foreground()})

        if p == '/health':
            ok, msg = ensure_device()
            return self._json({'ok': ok, 'device': msg, 'serial': STATE['serial'],
                               'container': STATE['container']})

        if p == '/reconnect':
            adb('kill-server', timeout=15)
            ok, msg = ensure_device()
            return self._send(200, '重连：%s（%s）' % ('成功' if ok else '失败', msg))

        if p == '/shot':
            with _LOCK:
                png = screenshot()
            if png:
                return self._send(200, png, 'image/png')
            if STATE['last_shot']:
                return self._send(200, STATE['last_shot'], 'image/png')
            return self._send(503, 'no screenshot')

        if p == '/swipe':
            # 滑块验证要靠拖动：/tap 只按一下拖不动
            try:
                x1 = int(q.get('x1', ['0'])[0]); y1 = int(q.get('y1', ['0'])[0])
                x2 = int(q.get('x2', ['0'])[0]); y2 = int(q.get('y2', ['0'])[0])
                ms = int(q.get('ms', ['600'])[0])
            except ValueError:
                return self._send(400, 'bad coords')
            adb('shell', 'input', 'swipe', str(x1), str(y1), str(x2), str(y2), str(ms), timeout=30)
            return self._send(200, 'drag %d,%d -> %d,%d' % (x1, y1, x2, y2))

        if p == '/tap':
            try:
                x = int(q.get('x', ['0'])[0]); y = int(q.get('y', ['0'])[0])
            except ValueError:
                return self._send(400, 'bad coords')
            adb('shell', 'input', 'tap', str(x), str(y), timeout=20)
            return self._send(200, 'tap %d,%d' % (x, y))

        if p == '/text':
            s = q.get('s', [''])[0]
            if not s:
                return self._send(400, 'empty')
            safe = s.replace(' ', '%s').replace('&', '\\&')
            adb('shell', 'input', 'text', safe, timeout=20)
            return self._send(200, '已输入 %d 个字符' % len(s))

        if p == '/key':
            k = q.get('k', ['4'])[0]
            if not k.isdigit():
                return self._send(400, 'bad key')
            adb('shell', 'input', 'keyevent', k, timeout=20)
            return self._send(200, 'key %s' % k)

        if p == '/swipe':
            try:
                x1 = int(q.get('x1', ['0'])[0]); y1 = int(q.get('y1', ['0'])[0])
                x2 = int(q.get('x2', ['0'])[0]); y2 = int(q.get('y2', ['0'])[0])
                ms = int(q.get('ms', ['300'])[0])
            except ValueError:
                return self._send(400, 'bad coords')
            adb('shell', 'input', 'swipe', str(x1), str(y1), str(x2), str(y2), str(ms), timeout=25)
            return self._send(200, 'swipe')

        return self._send(404, 'not found')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--serial', default='127.0.0.1:5555')
    ap.add_argument('--port', type=int, default=18091)
    ap.add_argument('--host', default='0.0.0.0')
    ap.add_argument('--adb', default='adb')
    ap.add_argument('--docker', default='docker')
    ap.add_argument('--container', default='soda-android13')
    ap.add_argument('--keepalive-unit', default='soda-keepalive')
    a = ap.parse_args()

    STATE['serial'] = a.serial
    STATE['adb'] = a.adb
    STATE['docker'] = a.docker
    STATE['container'] = a.container
    STATE['keepalive_unit'] = a.keepalive_unit

    ok, msg = ensure_device()
    print('[webui] device: %s (%s)' % ('OK' if ok else 'FAIL', msg), flush=True)
    print('[webui] account: %s' % json.dumps(read_account(), ensure_ascii=False), flush=True)

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print('[webui] http://%s:%d' % (a.host, a.port), flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
