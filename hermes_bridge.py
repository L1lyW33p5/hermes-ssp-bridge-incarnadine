#!/usr/bin/env python3
"""
Hermes SSP Bridge — 全局热键输入框 + SSP 修复
双击 Ctrl → 弹出/关闭 Listary 式悬浮输入框 → 发给当前 ghost

修复项:
  Bug 2: SSP 角色窗口置顶 → 枚举 SSP 进程所有可见窗口，全部置顶
  Bug 3: TTS 接管 → 禁用 Taromati2 TTS + bridge 用 Windows MCI 播放 Edge TTS 音频
  Bug 4: 守护进程 — 自动 kill wscript.exe 阻止 ghost VBScript TTS
"""
import ctypes, sys
import random, math
import json

# 命名 Mutex 单例 — 内核级原子
# 文件锁单例 — O_CREAT|O_EXCL 原子 + 旧锁检测
import os as _os, atexit as _atexit

_PROJECT_ROOT = _os.path.dirname(_os.path.abspath(__file__))
_LOCAL_ENV_FILE = _os.path.join(_PROJECT_ROOT, ".env")
try:
    with open(_LOCAL_ENV_FILE, encoding="utf-8") as _env_file:
        for _raw_env_line in _env_file:
            _env_line = _raw_env_line.strip()
            if not _env_line or _env_line.startswith("#") or "=" not in _env_line:
                continue
            _env_key, _env_value = _env_line.split("=", 1)
            _env_key = _env_key.strip()
            _env_value = _env_value.strip().strip('"').strip("'")
            if _env_key and _env_key not in _os.environ:
                _os.environ[_env_key] = _env_value
except OSError:
    pass

_SSP_ROOT = _os.path.abspath(_os.environ.get("HERMES_SSP_ROOT") or _PROJECT_ROOT)
_LOCK = _os.environ.get("HERMES_BRIDGE_LOCK") or _os.path.join(_SSP_ROOT, "hermes_bridge.lock")
_lock_ok = False
try:
    _fd = _os.open(_LOCK, _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY)
    _os.write(_fd, str(_os.getpid()).encode("utf-8"))
    _os.close(_fd)
    _lock_ok = True
except FileExistsError:
    # 锁文件存在，检查旧进程是否存活
    try:
        _old_pid = int(open(_LOCK).read().strip())
        import psutil as _psl
        if not _psl.pid_exists(_old_pid):
            _os.remove(_LOCK)
            _fd = _os.open(_LOCK, _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY)
            _os.write(_fd, str(_os.getpid()).encode("utf-8"))
            _os.close(_fd)
            _lock_ok = True
    except:
        pass
if not _lock_ok:
    sys.exit(0)
def _cleanup_lock():
    try: _os.remove(_LOCK)
    except: pass
_atexit.register(_cleanup_lock)

import os
import re
import socket
import subprocess
import sys
import threading
import time
import tempfile
import traceback
import shutil
from datetime import datetime
from pathlib import Path

import keyboard  # 全局热键
import win32gui
import win32con
import win32process
import psutil
import ctypes

# ─── 配置 ───
SSP_HOME = Path(_SSP_ROOT)
SSP_HOST = "127.0.0.1"
SSP_PORT = 9801
DOUBLE_PRESS_INTERVAL = 0.6
TTS_VOICE = "zh-CN-XiaoyiNeural"
TTS_ENABLED = True
_tts_lock = threading.Lock()
_tts_proc = None  # 当前 Windows MCI 播放器（提供 process-like 接口）
_tts_queue = []    # TTS 任务队列
_pre_generated_texts = set()  # 预生成文本集合——TTS watcher 跳过重复
_edge_audio_reason = ''  # EdgeAudio detail for logging
_random_talk_sid = None   # 随机谈话 session ID
_random_talk_time = 0     # 最近一次随机谈话触发时间

def read_nurturance_vars():
    """Read 7 nurturance variables — Ghost memory first, cfg fallback"""
    # Try Ghost memory via SSTP
    result = sstp_get_nurturance()
    if result is not None:
        return result

    # Fallback: read cfg snapshot
    import re
    cfg = SSP_HOME / "ghost" / "Taromati2" / "ghost" / "master" / "shiori" / "aya_variable.cfg"
    D=M=De=C=H=KM=INT=0
    try:
        with open(cfg, encoding='utf-8') as fh:
            txt = fh.read()
        for pat, target in [
            (r'nurturance\.Darkness,(\d+)', 'D'),
            (r'nurturance\.Moeness,(\d+)', 'M'),
            (r'nurturance\.Dependency,(\d+)', 'De'),
            (r'nurturance\.Closeness,(\d+)', 'C'),
            (r'nurturance\.Happiness,(\d+)', 'H'),
            (r'\nkikkamood,(\d+)', 'KM'),
            (r'\nintimacy,(\d+)', 'INT'),
        ]:
            m = re.search(pat, txt)
            if m:
                val = int(m.group(1))
                if target == 'D': D = val
                elif target == 'M': M = val
                elif target == 'De': De = val
                elif target == 'C': C = val
                elif target == 'H': H = val
                elif target == 'KM': KM = val
                elif target == 'INT': INT = val
    except Exception:
        pass
    return D, M, De, C, H, KM, INT


def compute_kikka_params():
    """Compute factor, cs, and sp from nurturance vars."""
    D, M, De, C, H, KM, INT = read_nurturance_vars()
    # factor: mean of 7 normalized
    f = (D/100 + M/100 + De/100 + C/100 + H/100 + KM/100 + INT/200) / 7
    # cs/sp: sim14 cycle length and curve sharpness derived from p and factor
    p = (INT + De + D + KM) / 100
    cs = 2 * p ** 2 / f if f > 0 else 999
    sp = cs * f if f > 0 else 999
    return f, cs, sp, (D, M, De, C, H, KM, INT)


# ── 全局共享 timer ── Screen/Talk watcher 跨线程复位用
_screen_timer = None      # 由 screen_watcher_loop 初始化
_talk_timer = None        # 由 random_talk_watcher_loop 初始化
_last_screen_trigger = 0.0  # 上次 Screen 触发时间戳
_pause_screen = threading.Event()
_pause_screen.set()  # 初始不暂停
_pause_talk = threading.Event()
_pause_talk.set()  # 初始不暂停
_user_screen_enabled = threading.Event()
_user_screen_enabled.set()
_user_talk_enabled = threading.Event()
_user_talk_enabled.set()
_watcher_control_lock = threading.Lock()
_watcher_control_state = {"talk": True, "screen": True}
_screen_pending_hotstart = False
_last_kikka_refresh = 0.0  # kikka wait 最后刷新时间戳（0=从未）

WATCHER_CONTROL_FILE = SSP_HOME / "bridge_workspace" / "watcher_control.json"


def _coerce_watcher_control(data):
    state = {}
    for key in ("talk", "screen"):
        value = data.get(key, True) if isinstance(data, dict) else True
        state[key] = bool(value)
    return state


def apply_watcher_control(state, *, log_changes=False):
    state = _coerce_watcher_control(state)
    with _watcher_control_lock:
        previous = dict(_watcher_control_state)
        _watcher_control_state.update(state)
    if state["talk"]:
        _user_talk_enabled.set()
    else:
        _user_talk_enabled.clear()
    if state["screen"]:
        _user_screen_enabled.set()
    else:
        _user_screen_enabled.clear()
    if log_changes and state != previous:
        log(f"watcher control updated: talk={state['talk']} screen={state['screen']}")
    return state


def read_watcher_control():
    try:
        if not WATCHER_CONTROL_FILE.exists():
            return dict(_watcher_control_state)
        data = json.loads(WATCHER_CONTROL_FILE.read_text(encoding="utf-8"))
        return _coerce_watcher_control(data)
    except Exception as exc:
        log(f"watcher control read failed: {exc}")
        return dict(_watcher_control_state)


def write_watcher_control(state):
    try:
        WATCHER_CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True)
        WATCHER_CONTROL_FILE.write_text(
            json.dumps(_coerce_watcher_control(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        log(f"watcher control write failed: {exc}")


def watcher_control_loop(interval: float = 0.5):
    state = read_watcher_control()
    apply_watcher_control(state, log_changes=True)
    write_watcher_control(state)
    while True:
        time.sleep(interval)
        apply_watcher_control(read_watcher_control(), log_changes=True)


def sstp_get_nurturance():
    """Query Ghost memory for real-time nurturance values.
    Sends NOTIFY OnGetNurturance -> Ghost writes hermes_nurturance_val.txt -> bridge reads file.
    Returns (D,M,De,C,H,KM,INT) or None on failure."""
    val_file = SSP_HOME / 'hermes_nurturance_val.txt'
    try:
        import socket as _sock, re as _re
        # Delete old file first (ensure fresh read)
        if val_file.exists():
            val_file.unlink()
        # Send NOTIFY to trigger handler
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        s.settimeout(2)
        s.connect((SSP_HOST, SSP_PORT))
        msg = "NOTIFY SSTP/1.1\r\nSender: Hermes\r\nEvent: OnGetNurturance\r\nCharset: UTF-8\r\n\r\n"
        s.sendall(msg.encode('utf-8'))
        s.recv(1024)  # consume response (empty, no need to parse)
        s.close()
        # Wait briefly for file write, then read
        import time
        for _ in range(10):
            if val_file.exists():
                break
            time.sleep(0.05)
        if val_file.exists():
            txt = val_file.read_text(encoding='utf-8').strip()
            vals = {}
            for key in ['Darkness','Moeness','Dependency','Closeness','Happiness','kikkamood','intimacy']:
                m = _re.search(key + r'=(\d+)', txt)
                if m:
                    vals[key] = int(m.group(1))
            if len(vals) == 7:
                return (vals['Darkness'], vals['Moeness'], vals['Dependency'],
                        vals['Closeness'], vals['Happiness'], vals['kikkamood'], vals['intimacy'])
    except Exception:
        pass
    return None


LOG_FILE = SSP_HOME / "hermes_bridge.log"

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# SSP 窗口置顶守护
# ═══════════════════════════════════════════════════════════════

def get_ssp_pids():
    pids = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["name"] and proc.info["name"].lower() == "ssp.exe":
                pids.append(proc.info["pid"])
        except Exception:
            pass
    return pids


def find_ssp_windows():
    ssp_pids = set(get_ssp_pids())
    if not ssp_pids:
        return []
    result = []
    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid in ssp_pids:
            title = win32gui.GetWindowText(hwnd)
            cls = win32gui.GetClassName(hwnd)
            if title or cls:
                result.append((hwnd, title, cls))
    win32gui.EnumWindows(callback, None)
    return result


def set_ssp_topmost():
    windows = find_ssp_windows()
    if not windows:
        log("⚠️ 置顶: 未找到 SSP 窗口")
        return
    for hwnd, title, cls in windows:
        try:
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOPMOST,
                0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
            )
            log(f"✅ 置顶: [{cls}] {title}")
        except Exception as e:
            log(f"⚠️ 置顶失败 [{cls}] {title}: {e}")


def ensure_ssp_topmost_loop(interval: float = 3.0):
    while True:
        time.sleep(interval)
        try:
            windows = find_ssp_windows()
            for hwnd, title, cls in windows:
                exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                if not (exstyle & win32con.WS_EX_TOPMOST):
                    win32gui.SetWindowPos(
                        hwnd, win32con.HWND_TOPMOST,
                        0, 0, 0, 0,
                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
                    )
                    log(f"🔄 恢复置顶: [{cls}] {title}")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# wscript.exe 进程守护 — 阻止 ghost VBScript TTS
# ═══════════════════════════════════════════════════════════════

def kill_ghost_tts_procs():
    """只终止 Taromati2 voice.vbs 启动的 wscript.exe。"""
    killed = 0
    taromati_root = str(SSP_HOME / "ghost" / "Taromati2").lower()
    for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
        try:
            if proc.info["name"] and proc.info["name"].lower() == "wscript.exe":
                cmd = " ".join(proc.info["cmdline"] or []).lower()
                cwd = str(proc.info.get("cwd") or "").lower()
                if "voice.vbs" in cmd and (taromati_root in cmd or cwd.startswith(taromati_root)):
                    proc.kill()
                    killed += 1
                    log(f"🔇 已终止 ghost TTS: wscript.exe (PID={proc.info['pid']})")
        except Exception:
            pass
    return killed


def wscript_watcher_loop(interval: float = 0.5):
    """高频轮询，发现 wscript.exe 立即杀"""
    while True:
        time.sleep(interval)
        try:
            kill_ghost_tts_procs()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# TTS 队列文件监听 — ghost 写入文本 → bridge 用 Edge TTS 朗读
# ═══════════════════════════════════════════════════════════════

TTS_QUEUE_FILE = SSP_HOME / "hermes_tts_queue.txt"
_tts_queue_pos = None  # None = 启动时跳到文件末尾
# 固定 TTS_GAP 已移除；播放期间由队列轮询实现即时打断
AITALK_INTERVAL = 180
KIKKA_WAIT_SECS = 180          # kikka 等待回复时间
HALF_INTERVAL = AITALK_INTERVAL / 2   # = 90
LOCK_VAL = "99999"  # 锁文件写入值  # kikka 谈话静默锁


def clean_sakura_text(text: str) -> str:
    """清洗 Sakura Script 残留转义符，保留纯文本"""
    import re
    t = text
    # 所有 Sakura Script 标签（贪婪匹配到 ] 或空格/结束）
    # \s[N] \w[N] \b[N] \p[N] \f[...] \n \n[N] \v \v[N] \h \u \t
    t = re.sub(r'\\[swbpfhnvut]\s*(\[[^\]]*\])?', '', t)
    # 独立转义符: \e \x \C \0 \1 \- \c \_
    t = re.sub(r'\\[exC01\-c_]', '', t)
    # 下划线系列: \_q \_w[...] \_a[...] \_v[...] \_n \_l \_s
    t = re.sub(r'\\_[qwalnvsub]\s*(\[[^\]]*\])?', '', t)
    # 感叹号系列: \![...]  多层嵌套
    t = re.sub(r'\\!\[(?:[^\[\]]|\[[^\]]*\])*\]', '', t)
    # \q[...] 和 \j[...] 等
    t = re.sub(r'\\[qj]\s*\[(?:[^\[\]]|\[[^\]]*\])*\]', '', t)
    # 残余的所有 \字母 \数字 \符号
    t = re.sub(r'\\(?:[a-zA-Z0-9]|\[[^\]]*\])', '', t)
    # 残余的独立 \
    t = t.replace('\\', '')
    # 清理多余空白
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    t = t.strip()
    return t



def drop_queue_watcher_loop(interval: float = 0.5):
    """监听文字拖入队列，发给 Hermes AI"""
    queue_file = SSP_HOME / "hermes_drop_queue.txt"
    # 先处理已有内容（防止重启丢失）
    if queue_file.exists():
        with open(queue_file, "r", encoding="utf-8") as f:
            old = f.read().strip()
        if old:
            for line in old.split("\n"):
                line = line.strip()
                if line:
                    log(f"📥 Drop (catch-up): {line[:60]}")
                    query = f'"{line}"kikka怎么评价？'
                    try:
                        reply = hermes_ask(query)
                        if reply:
                            tts = generate_tts(reply)
                            start_buffer_lock()
                            ghost_say(reply, tts_file=tts)
                    except Exception as de:
                        log(f"⚠️ Drop catch-up error: {de}")
        open(queue_file, "w", encoding="utf-8").close()
    pos = 0
    while True:
        time.sleep(interval)
        try:
            if not queue_file.exists():
                continue
            with open(queue_file, "r", encoding="utf-8") as f:
                f.seek(pos)
                lines_data = f.readlines()
                pos = f.tell()
            # 合并本次所有行（一次拖入可能多行），清空文件
            all_text = "".join(lines_data).strip()
            if not all_text:
                continue
            # 清空文件避免重复
            open(queue_file, "w", encoding="utf-8").close()
            pos = 0
            # 清理 \n 残留
            while all_text.endswith('\\n'):
                all_text = all_text[:-2]
            # 清理尾随单反斜杠
            all_text = all_text.rstrip('\\')
            all_text = all_text.strip()
            if all_text:
                log(f"📥 Drop: {all_text[:60]}...")
                query = f'"{all_text}"kikka怎么评价？'
                _tts_queue.clear()
                global _tts_queue_pos
                _tts_queue_pos = 0
                if TTS_QUEUE_FILE.exists():
                    TTS_QUEUE_FILE.write_text("", encoding="utf-8")
                reply = hermes_ask(query)
                if reply:
                    tts = generate_tts(reply)
                    start_buffer_lock()
                ghost_say(reply, tts_file=tts)
        except Exception as e:
            log(f"⚠️ Drop watcher error: {e}")
            time.sleep(1)



def image_queue_watcher_loop(interval: float = 0.5):
    """监听图片拖入队列，base64 编码发给 Hermes AI"""
    queue_file = SSP_HOME / "hermes_image_queue.txt"
    if queue_file.exists():
        with open(queue_file, "r", encoding="utf-8") as f:
            f.seek(0, 2)
            pos = f.tell()
    else:
        pos = 0
    while True:
        time.sleep(interval)
        try:
            if not queue_file.exists():
                continue
            with open(queue_file, "r", encoding="utf-8") as f:
                import os as _os
                actual_size = _os.path.getsize(str(queue_file))
                if pos > actual_size:
                    pos = 0
                f.seek(pos)
                lines_data = f.readlines()
                pos = f.tell()
            if lines_data:
                open(queue_file, "w").close()
                pos = 0
            for line in lines_data:
                path = line.strip()
                if not path or not os.path.exists(path):
                    continue
                log(f"📥 Image: {path}")
                try:
                    import base64, mimetypes
                    mime = mimetypes.guess_type(path)[0] or "image/png"
                    with open(path, "rb") as img:
                        b64 = base64.b64encode(img.read()).decode()
                    data_url = f"data:{mime};base64,{b64}"
                    reply = hermes_ask_multimodal("kikka看看这个", data_url)
                    if reply:
                        tts = generate_tts(reply)
                        ghost_say(reply, tts_file=tts)
                except Exception as ie:
                    log(f"⚠️ Image error: {ie}")
        except Exception as e:
            log(f"⚠️ Image watcher: {e}")
            time.sleep(1)



def tts_queue_watcher_loop(interval: float = 0.3):
    global _pre_generated_texts  # Python 作用域：discard() 触发 local binding
    """监听桥接队列文件，新行用 Edge TTS 朗读"""
    global _tts_queue_pos
    while True:
        time.sleep(interval)
        try:
            if not TTS_QUEUE_FILE.exists():
                continue
            with open(TTS_QUEUE_FILE, "r", encoding="utf-8", errors="replace") as f:
                f.seek(_tts_queue_pos)
                new_lines = f.readlines()
                if new_lines:
                    _tts_queue_pos = f.tell()
                    for line in new_lines:
                        raw = line.strip()
                        if raw:
                            # 图片拖入：……前缀（清洗前拦截，防路径被 clean_sakura_text 破坏）
                            if raw.startswith('……') and len(raw) > 2:
                                img_path = raw[2:].strip()
                                while img_path.endswith('\\n') or img_path.endswith('\\'):
                                    img_path = img_path[:-2] if img_path.endswith('\\n') else img_path[:-1]
                                img_path = img_path.strip()
                                log(f"📥 Image (via TTS): {img_path}")
                                # HTTP(S) URL → 直接传给 multimodal
                                if img_path.startswith('http://') or img_path.startswith('https://'):
                                    try:
                                        reply = hermes_ask_multimodal("kikka看看这个", img_path)
                                        if reply:
                                            tts = generate_tts(reply)
                                            start_buffer_lock()
                                            ghost_say(reply, tts_file=tts)
                                    except Exception as ie:
                                        log(f"⚠️ Image error: {ie}")
                                    continue
                                # 本地文件 → base64 编码
                                if os.path.exists(img_path):
                                    try:
                                        import base64, mimetypes
                                        mime = mimetypes.guess_type(img_path)[0] or "image/png"
                                        with open(img_path, "rb") as img:
                                            b64 = base64.b64encode(img.read()).decode()
                                        data_url = f"data:{mime};base64,{b64}"
                                        reply = hermes_ask_multimodal("kikka看看这个", data_url)
                                        if reply:
                                            tts = generate_tts(reply)
                                            start_buffer_lock()
                                            ghost_say(reply, tts_file=tts)
                                    except Exception as ie:
                                        log(f"⚠️ Image error: {ie}")
                                    continue
                            text = clean_sakura_text(raw)
                            if re.match(r"^[.…。]+$", text):
                                continue
                            if text:
                                if text in _pre_generated_texts:
                                    _pre_generated_texts.discard(text)
                                    continue
                                log(f"📥 Ghost TTS: \"{text[:60]}{'...' if len(text) > 60 else ''}\"")
                                # 按非空段落入队；播放中如有新内容由 worker 立即打断
                                paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
                                for para in paragraphs:
                                    speak_tts(para)
        except Exception as e:
            log(f"⚠️ TTS watcher error: {e}")
            time.sleep(1)


# ═══════════════════════════════════════════════════════════════
# TTS 接管
# ═══════════════════════════════════════════════════════════════

_RUNTIME_BACKUP_STAMP = time.strftime("%Y%m%d-%H%M%S")
_RUNTIME_BACKED_UP = set()


def _backup_runtime_file(path: Path):
    """在首次改写 SSP/Taromati2 运行时配置前保存一份副本。"""
    resolved = path.resolve()
    key = str(resolved).lower()
    if key in _RUNTIME_BACKED_UP or not resolved.exists():
        return
    try:
        relative = resolved.relative_to(SSP_HOME.resolve())
    except ValueError:
        relative = Path(resolved.name)
    target = SSP_HOME / "patch-backups" / _RUNTIME_BACKUP_STAMP / "runtime" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(resolved, target)
    _RUNTIME_BACKED_UP.add(key)
    log(f"🛟 运行时配置备份: {target}")


def disable_ssp_baseware_tts():
    """禁用 SSP baseware 内置 TTS"""
    app_dat = SSP_HOME / "data" / "profile" / "app.dat"
    if not app_dat.exists():
        return
    content = app_dat.read_text(encoding="utf-8")
    new_content = re.sub(r'^voice\.level,.*$', 'voice.level,0', content, flags=re.MULTILINE)
    if new_content != content:
        _backup_runtime_file(app_dat)
        app_dat.write_text(new_content, encoding="utf-8")
        log("✅ TTS: SSP baseware voice.level → 0")
    else:
        log("   TTS: SSP baseware voice.level 已为 0")


def disable_ghost_tts():
    """仅调整 Taromati2 的 YAYA TTS/随机谈话相关变量。"""
    cfg_path = SSP_HOME / "ghost" / "Taromati2" / "ghost" / "master" / "shiori" / "aya_variable.cfg"
    if not cfg_path.exists():
        return
    try:
        content = cfg_path.read_text(encoding="utf-8")
        new_content = re.sub(r'^aitalkinterval,\d+', f'aitalkinterval,{AITALK_INTERVAL},",",', content, flags=re.MULTILINE)
        new_content = re.sub(r'^voice,.*', 'voice,1,",",', new_content, flags=re.MULTILINE)
        new_content = re.sub(r'^acjeachday,1,', 'acjeachday,0,', new_content, flags=re.MULTILINE)
        new_content = re.sub(r'^japwordeachday,1,', 'japwordeachday,0,', new_content, flags=re.MULTILINE)
        if new_content != content:
            _backup_runtime_file(cfg_path)
            cfg_path.write_text(new_content, encoding="utf-8")
            log("✅ TTS: Taromati2 voice → 1, daily learning → 0")
    except Exception as e:
        log(f"⚠️ TTS: {e}")


def _mci_send(command: str, response_chars: int = 0) -> str:
    """Run a Windows MCI command and raise a useful error on failure."""
    response = ctypes.create_unicode_buffer(max(1, response_chars))
    error = ctypes.windll.winmm.mciSendStringW(
        command,
        response if response_chars else None,
        response_chars,
        None,
    )
    if error:
        message = ctypes.create_unicode_buffer(256)
        ctypes.windll.winmm.mciGetErrorStringW(error, message, len(message))
        raise OSError(f"MCI error {error}: {message.value or command}")
    return response.value if response_chars else ""


class _MciAudioPlayer:
    """Small process-like wrapper around Windows' built-in MP3 playback."""

    def __init__(self, path: str):
        self.alias = f"hermes_tts_{time.time_ns()}"
        self._closed = False
        _mci_send(f'open "{os.path.abspath(path)}" type mpegvideo alias {self.alias}')
        try:
            _mci_send(f"play {self.alias}")
        except Exception:
            self.kill()
            raise

    def poll(self):
        if self._closed:
            return 0
        try:
            mode = _mci_send(f"status {self.alias} mode", 64).strip().lower()
        except OSError:
            self._closed = True
            return 1
        if mode in {"playing", "paused", "seeking"}:
            return None
        self.kill()
        return 0

    def kill(self):
        if self._closed:
            return
        try:
            _mci_send(f"stop {self.alias}")
        except OSError:
            pass
        try:
            _mci_send(f"close {self.alias}")
        except OSError:
            pass
        self._closed = True

    def wait(self):
        while self.poll() is None:
            time.sleep(0.05)
        return 0


def _start_audio_player(path: str):
    return _MciAudioPlayer(path)


def _new_temp_mp3() -> str:
    handle = tempfile.NamedTemporaryFile(prefix="hermes-tts-", suffix=".mp3", delete=False)
    path = handle.name
    handle.close()
    return path


def _tts_worker():
    """单线程 TTS worker — FIFO 入队，播放中可被新内容打断"""
    global _tts_queue, _tts_proc


    while True:
        # 按入队顺序取下一条；播放中检测到新内容时会打断当前播放
        if not _tts_queue:
            time.sleep(0.1)
            continue
        text = _tts_queue.pop(0)
        if text is None:
            time.sleep(0.1)
            continue

        clean = text.strip()
        log(f"🔊 TTS: \"{clean[:60]}{'...' if len(clean) > 60 else ''}\"")
        tmpfile = None
        try:
            # 打断当前播放
            with _tts_lock:
                if _tts_proc is not None:
                    try:
                        _tts_proc.kill()
                    except Exception:
                        pass
                    _tts_proc = None



            # 合成（最多重试 5 次，刚开机网络未就绪）
            for attempt in range(1, 6):
                tmpfile = _new_temp_mp3()
                cmd = [sys.executable, "-m", "edge_tts", "--voice", TTS_VOICE,
                       "--rate=-6%", "--text", clean, "--write-media", tmpfile]
                t0 = time.time()
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0 and os.path.exists(tmpfile) and os.path.getsize(tmpfile) > 0:
                    log(f"   TTS: 合成 {os.path.getsize(tmpfile)} bytes ({time.time()-t0:.1f}s, attempt {attempt})")
                    break
                log(f"   TTS: 重试 {attempt}/5 (rc={result.returncode})")
                try:
                    os.unlink(tmpfile)
                except OSError:
                    pass
                tmpfile = None
                time.sleep(3)
            else:
                log(f"❌ TTS: edge-tts 失败 (共 5 次)")
                continue
            if not os.path.exists(tmpfile) or os.path.getsize(tmpfile) == 0:
                log("❌ TTS: 文件为空")
                continue

            # 使用 Windows 内置 MCI 播放，无需额外安装 ffplay。
            with _tts_lock:
                _tts_proc = _start_audio_player(tmpfile)

            # 播中轮询队列——有新内容立即打断
            while _tts_proc is not None and _tts_proc.poll() is None:
                if _tts_queue:
                    with _tts_lock:
                        try:
                            _tts_proc.kill()
                        except Exception:
                            pass
                        _tts_proc = None
                    break
                time.sleep(0.2)
            log(f"   TTS: 播放完成 ({time.time()-t0:.1f}s)")
        except Exception as e:
            log(f"❌ TTS worker: {e}")
        finally:
            if tmpfile and os.path.exists(tmpfile):
                try:
                    os.unlink(tmpfile)
                except Exception:
                    pass


def generate_tts(text: str) -> str | None:
    """同步生成 TTS 音频，返回 MP3 文件路径（用于 Hermes 回复优先）"""
    clean = text.strip()
    if not clean:
        return None
    for attempt in range(1, 6):
        tmpfile = _new_temp_mp3()
        cmd = [sys.executable, "-m", "edge_tts", "--voice", TTS_VOICE,
               "--rate=-6%", "--text", clean, "--write-media", tmpfile]
        t0 = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and os.path.exists(tmpfile) and os.path.getsize(tmpfile) > 0:
            log(f"   TTS 预合成: {os.path.getsize(tmpfile)} bytes ({time.time()-t0:.1f}s, attempt {attempt})")
            return tmpfile
        log(f"   TTS 预合成: 重试 {attempt}/5 (rc={result.returncode})")
        try:
            os.unlink(tmpfile)
        except OSError:
            pass
        time.sleep(3)
    log("❌ TTS 预合成: edge-tts 失败 (5 次)")
    return None


def _play_tts_file(tmpfile: str):
    """异步播放预生成 TTS 音频，支持打断"""
    global _tts_proc, _tts_queue, _tts_queue_pos
    _tts_queue.clear()
    if TTS_QUEUE_FILE.exists():
        TTS_QUEUE_FILE.write_text("", encoding="utf-8")
    _tts_queue_pos = 0
    with _tts_lock:
        if _tts_proc and _tts_proc.poll() is None:
            try:
                _tts_proc.kill()
            except Exception:
                pass
            _tts_proc = None
        _tts_proc = _start_audio_player(tmpfile)
    if _tts_proc is not None:
        _tts_proc.wait()
    with _tts_lock:
        _tts_proc = None
    try:
        os.unlink(tmpfile)
    except Exception:
        pass


def speak_tts(text: str):
    """将文本加入 TTS 队列（串行播放，不叠音）"""
    if not TTS_ENABLED or not text or not text.strip():
        return
    _tts_queue.append(text.strip())


# ═══════════════════════════════════════════════════════════════
# SSTP 通信
# ═══════════════════════════════════════════════════════════════

def sstp_send(script: str) -> str:
    _sstp_lock.acquire()
    msg = (
        f"SEND SSTP/1.4\r\n"
        f"Sender: Hermes\r\n"
        f"Charset: UTF-8\r\n"
        f"Script: {script}\r\n"
        f"\r\n"
    )
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect((SSP_HOST, SSP_PORT))
            s.send(msg.encode("utf-8"))
            return s.recv(4096).decode("utf-8", errors="replace")
    except Exception as e:
        log(f"❌ SSTP: {e}")
        return f"ERROR: {e}"
    finally:
        _sstp_lock.release()


# Hermes AI — 通过 Gateway API Server 对话
HERMES_API = os.environ.get(
    "HERMES_API_URL",
    "http://127.0.0.1:8642/v1/chat/completions",
).strip()

# Daily memory backup
_DEFAULT_HERMES_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "hermes"
_KIKKA_PROFILE_DIR = Path(
    os.environ.get("HERMES_GATEWAY_HOME") or (_DEFAULT_HERMES_ROOT / "profiles" / "kikka")
).expanduser()
_HERMES_ROOT = _KIKKA_PROFILE_DIR.parent.parent
_KIKKA_MEM_DIR = _KIKKA_PROFILE_DIR / "memories"
_BACKUP_ROOT = _HERMES_ROOT / "backup" / "kikka"
_KIKKA_SOUL_FILE = _KIKKA_PROFILE_DIR / "SOUL.md"
_BACKUP_FILES = [_KIKKA_SOUL_FILE, _KIKKA_MEM_DIR / "MEMORY.md", _KIKKA_MEM_DIR / "USER.md"]
_last_backup_date = ""
_last_backup_hashes = {}

HERMES_KEY = None  # 从 .env 读取

def _get_key():
    global HERMES_KEY
    if HERMES_KEY:
        return HERMES_KEY
    candidates = [
        _KIKKA_PROFILE_DIR / ".env",
        _HERMES_ROOT / ".env",
        Path.home() / "AppData" / "Local" / "hermes" / ".env",
    ]
    seen = set()
    for env_path in candidates:
        resolved = str(env_path.resolve()).lower()
        if resolved in seen or not env_path.exists():
            continue
        seen.add(resolved)
        for line in env_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            if line.upper().startswith("API_SERVER_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    HERMES_KEY = value
                    return HERMES_KEY
    return ""

def capture_screen() -> str:
    """截取显示器1，返回 base64 JPEG data URL（原分辨率，质量100）"""
    import mss as _mss
    import base64 as _b64
    from io import BytesIO as _BytesIO
    from PIL import Image as _PILImage
    with _mss.MSS() as _sct:
        _img = _sct.grab(_sct.monitors[1])
    _pil = _PILImage.frombytes('RGB', _img.size, _img.bgra, 'raw', 'BGRX')
    _buf = _BytesIO()
    _pil.save(_buf, format='JPEG', quality=100)
    return f"data:image/jpeg;base64,{_b64.b64encode(_buf.getvalue()).decode()}"


def is_non_whitelist_audio_playing() -> bool:
    """IAudioMeterInformation.GetPeakValue() + music whitelist"""
    global _tts_proc, _edge_audio_reason
    if _tts_proc is not None and _tts_proc.poll() is None:
        _edge_audio_reason = 'self-TTS'
        return True
    try:
        from pycaw.pycaw import AudioUtilities
        from pycaw.api.endpointvolume import IAudioMeterInformation
        MUSIC_APPS = ['qqmusic.exe', 'cloudmusic.exe']
        other_peak = 0.0
        music_peak = 0.0
        other_name = ''
        music_name = ''
        for s in AudioUtilities.GetAllSessions():
            try:
                if not s.Process:
                    continue
                name = s.Process.name().lower()
                meter = s._ctl.QueryInterface(IAudioMeterInformation)
                peak = meter.GetPeakValue()
                if peak > 0.001:
                    if name in MUSIC_APPS:
                        if peak > music_peak:
                            music_peak = peak
                            music_name = name
                    else:
                        if peak > other_peak:
                            other_peak = peak
                            other_name = name
            except Exception:
                pass
        if other_peak > 0.001:
            _edge_audio_reason = f'other:{other_name}({other_peak:.2f})'
            return True
        if music_peak > 0.001:
            _edge_audio_reason = f'music:{music_name}({music_peak:.2f})'
        else:
            _edge_audio_reason = 'none'
        return False
    except Exception:
        _edge_audio_reason = 'error'
        return False


def random_talk_watcher_loop():
    """Sine-wave factor-driven random talk — dual gate (probability + audio)"""
    log("💬 Talk watcher started (60s init)")
    global _random_talk_sid, _random_talk_time
    global _talk_timer
    time.sleep(60)

    # Talk timer starts cold (0); Screen timer starts hot at cs/2
    talk_timer = 0.0
    _talk_timer = talk_timer

    while True:
        _user_talk_enabled.wait()
        _pause_talk.wait()
        time.sleep(30)
        _user_talk_enabled.wait()
        _pause_talk.wait()
        talk_timer += 1
        try:
            factor, cs, sp, vars_tuple = compute_kikka_params()
            D, M, De, C, H, KM, INT = vars_tuple
            if cs <= 1:
                continue

            # Sine-wave probability
            phase = (talk_timer % cs) / cs
            prob = factor * math.sin(math.pi * phase) ** sp
            prob = max(0.0, prob)

            audio_block = is_non_whitelist_audio_playing()

            log(f"💬 Talk: factor={factor:.3f} cs={cs:.0f} sp={sp:.0f} "
                f"phase={phase:.3f} prob={prob:.3f} Audio={_edge_audio_reason} "
                f"D={D} M={M} De={De} C={C} H={H} KM={KM} INT={INT}")

            if random.random() >= prob:
                continue  # formula dice missed → timer keeps going

            # Formula hit — consume peak
            talk_timer = 0.0

            # Delay+reset in background — always, regardless of trigger_ok
            def _async_delay_reset():
                global _screen_timer, _last_screen_trigger, _pause_screen, _pause_talk, _screen_pending_hotstart, _last_kikka_refresh
                gap = time.time() - _last_screen_trigger
                if gap > 0 and _screen_timer is not None:
                    log(f"⏸️ Pausing watchers for {gap:.0f}s (sim14 delay-reset)")
                    _pause_screen.clear()
                    _pause_talk.clear()
                    time.sleep(gap)
                    # 检查 kikka 是否在活跃对话中（3min 内被刷新），是则延长 delay
                    while True:
                        if _last_kikka_refresh == 0:
                            break  # 从未刷新 → 正常 resume
                        elapsed = time.time() - _last_kikka_refresh
                        if elapsed > 180:
                            break  # 超过 3min → 正常 resume
                        log(f"⏸️ Kikka active ({elapsed:.0f}s ago), extending delay by {gap:.0f}s")
                        time.sleep(gap)
                    _, cs, _, _ = compute_kikka_params()
                    if cs > 0:
                        _screen_timer = cs / 2
                    _screen_pending_hotstart = True
                    _pause_screen.set()
                    _pause_talk.set()
                    log("▶️ Watchers resumed")
                    _last_screen_trigger = 0.0
                    log("🔄 Screen delay-reset complete, screen timestamp consumed")
            threading.Thread(target=_async_delay_reset, daemon=True).start()

            # Audio gate + 5s double-check (at hit time)
            trigger_ok = True
            if audio_block:
                log("💬 Talk: formula hit but audio blocked")
                trigger_ok = False
            else:
                log("💬 Talk: 5s double-check starting")
                time.sleep(5)
                if is_non_whitelist_audio_playing():
                    log(f"💬 Talk: 5s double-check blocked ({_edge_audio_reason})")
                    trigger_ok = False
                else:
                    log("💬 Talk: 5s double-check passed (none)")

            if not trigger_ok:
                log(f"💬 Talk: formula hit but blocked by audio ({_edge_audio_reason})")
                continue

            log("💬 Talk triggered!")

            try:
                _random_talk_sid = f"ssp-rt-{time.time_ns()}"
                _random_talk_time = time.time()
                ts = time.strftime('%Y-%m-%d %H:%M')
                prompt = "ㅤ"
                reply = hermes_ask(prompt, session_id=_random_talk_sid,
                                   system_override=f"你是橘花，现在是 {ts}，你有点无聊。请用一个主人不知道的新话题自然地开启对话，无需提到任何过去的共同经历。")
                if reply:
                    reply = reply.replace("\n", "")  # 合并多行为一句，防止 TTS 分段
                    set_voice(0)
                    log(f"🔒 永恒锁: ON ({LOCK_VAL})")
                    (SSP_HOME / "ghost" / "Taromati2" / "ghost" / "master" / "hermes_lock_val.txt").write_text(LOCK_VAL)
                    tts = generate_tts(reply)
                    ghost_say(reply, tts_file=tts, persistent=True)
                    threading.Thread(target=kikka_wait_thread, daemon=True).start()
            except Exception as e:
                log(f"💬 Talk failed: {e}")
        except Exception as e:
            log(f"💬 Talk check error: {e}")


def set_voice(val: int):
    """临时设置 ghost voice 变量来控制发言"""
    try:
        cfg_path = SSP_HOME / "ghost" / "Taromati2" / "ghost" / "master" / "shiori" / "aya_variable.cfg"
        if cfg_path.exists():
            content = cfg_path.read_text(encoding="utf-8")
            new_content = re.sub(r'^voice,.*', f'voice,{val},",",', content, flags=re.MULTILINE)
            if new_content != content:
                cfg_path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        log(f"⚠️ set_voice({val}): {e}")
def screen_watcher_loop():
    """Sine-wave driven screen capture — timer starts at cs/2 (hot), same formula as talk"""
    log("📸 Screen watcher started (60s init)")
    import time, math
    global _screen_timer, _screen_pending_hotstart

    # Screen timer starts at cs/2 — hot, fires first (observe before talk)
    _, cs, sp, _ = compute_kikka_params()
    screen_timer = cs / 2 if cs > 0 else 0
    _screen_timer = screen_timer

    time.sleep(60)  # initial settle

    while True:
        _user_screen_enabled.wait()
        _pause_screen.wait()      # 进入周期前检查 Talk 是否在 delay
        time.sleep(30)
        _user_screen_enabled.wait()
        _pause_screen.wait()      # 睡醒后再检查一次（防竞态）
        if _screen_pending_hotstart:
            screen_timer = _screen_timer
            _screen_pending_hotstart = False
        screen_timer += 1

        # Refresh params each cycle
        factor, cs, sp, vars_tuple = compute_kikka_params()
        D, M, De, C, H, KM, INT = vars_tuple
        if cs <= 1:
            continue

        # Sine-wave probability
        phase = (screen_timer % cs) / cs
        prob = factor * math.sin(math.pi * phase) ** sp
        prob = max(0.0, prob)

        log(f"📸 Screen: factor={factor:.3f} cs={cs:.0f} sp={sp:.0f} "
                f"phase={phase:.3f} prob={prob:.3f} "
                f"D={D} M={M} De={De} C={C} H={H} KM={KM} INT={INT}")

        if random.random() >= prob:
            continue


        # Trigger screen capture — only first Screen in this loop sets timestamp
        global _last_screen_trigger
        if _last_screen_trigger > 0:
            # timestamp already set, block to prevent overwrite — Talk will consume it
            log("📸 Screen: collision suppressed (waiting for Talk delay-reset)")
            screen_timer = 0.0           # consume peak
            continue
        log("📸 Screen triggered!")
        screen_timer = 0.0
        _last_screen_trigger = time.time()


        try:
            # if not is_gateway_healthy():
            #     log("⚠️ Gateway unreachable, skip screen capture")
            #     continue

            start_buffer_lock()
            ghost_say("让我看看……", clear=False)
            data_url = capture_screen()
            reply = hermes_ask_multimodal(
                f"（{time.strftime('%Y-%m-%d %H:%M')} 你观察了一下主人的屏幕，发现主人现在正在）",
                data_url
            )
            if reply:
                reply = reply.replace('\n', '')
                tts = generate_tts(reply)
                _tts_queue.clear()
                global _tts_queue_pos
                _tts_queue_pos = 0
                if TTS_QUEUE_FILE.exists():
                    TTS_QUEUE_FILE.write_text("", encoding="utf-8")
                set_voice(0)
                ghost_say(reply, tts_file=tts)
            log("📸 Screen capture complete")
        except Exception as e:
            log(f"⚠️ Screen capture failed: {e}")




def maybe_daily_backup():
    global _last_backup_date, _last_backup_hashes
    import hashlib as _hl, shutil as _sh
    today = time.strftime('%y%m%d')
    if today == _last_backup_date:
        return
    changed = False
    for f in _BACKUP_FILES:
        if f.exists():
            h = _hl.md5(f.read_bytes()).hexdigest()
            if h != _last_backup_hashes.get(str(f), ''):
                changed = True
                _last_backup_hashes[str(f)] = h
        else:
            _last_backup_hashes.pop(str(f), None)
    if not changed:
        _last_backup_date = today
        return
    dest = _BACKUP_ROOT / today
    dest.mkdir(parents=True, exist_ok=True)
    mem_dest = dest / 'memories'
    mem_dest.mkdir(exist_ok=True)
    try:
        for f in _BACKUP_FILES:
            if f.exists():
                target = mem_dest / f.name if 'memories' in str(f) else dest / f.name
                target.write_bytes(f.read_bytes())
        log('daily backup -> ' + today)
    except Exception as e:
        log('backup failed: ' + str(e))
    _last_backup_date = today
    cleanup_old_backups()

def cleanup_old_backups():
    import shutil as _sh
    cutoff = time.strftime('%y%m%d', time.localtime(time.time() - 30 * 86400))
    try:
        for d in os.listdir(str(_BACKUP_ROOT)):
            dp = _BACKUP_ROOT / d
            if dp.is_dir() and len(d) == 6 and d.isdigit() and d < cutoff:
                _sh.rmtree(dp)
                log('cleaned old backup: ' + d)
    except Exception as e:
        log('backup cleanup failed: ' + str(e))

def hermes_ask(text: str, session_id: str | None = None, system_override: str | None = None) -> str:
    """调用 Hermes AI 进行对话，返回回复文本"""
    maybe_daily_backup()
    import urllib.request, json, uuid, time as _time
    try:
        key = _get_key()
        data = json.dumps({
            "model": "hermes-agent",
            "messages": [
                {"role": "system", "content": system_override or "你是橘花（kikka）。主人说："},
                {"role": "user", "content": text}
            ],
        }).encode("utf-8")
        req = urllib.request.Request(HERMES_API, data=data, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "X-Hermes-Session-Id": session_id or f"ssp-{_time.time_ns()}",
        })
        resp = urllib.request.urlopen(req, timeout=120)
        raw = resp.read().decode('utf-8', errors='replace')
        body = json.loads(raw)
        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        log(f"   Hermes: {len(content)} chars")
        if not content:
            log(f"   Hermes RAW: {raw[:300]}")
        return content.strip() or "[Hermes 空响应]"
    except Exception as e:
        log(f"❌ Hermes API: {e}")
        return f"[Hermes API 错误: {e}]"


def hermes_ask_multimodal(text: str, image_data_url: str) -> str:
    """Multimodal 请求：文本 + 图片"""
    maybe_daily_backup()
    import urllib.request, json
    try:
        key = _get_key()
        data = json.dumps({
            "model": "hermes-agent",
            "messages": [
                {"role": "system", "content": "你是橘花（kikka）。\n" + text},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": image_data_url}}
                ]}
            ],
        }).encode("utf-8")
        req = urllib.request.Request(HERMES_API, data=data, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        })
        resp = urllib.request.urlopen(req, timeout=300)
        body = json.loads(resp.read().decode('utf-8', errors='replace'))
        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content.strip() or "[Hermes 空响应]"
    except Exception as e:
        log(f"❌ Hermes multimodal: {e}")
        return f"[Hermes multimodal 错误: {e}]"




def sstp_notify(event_id: str, ref0: str = "", ref1: str = "", ref2: str = "", ref3: str = "", ref4: str = ""):
    """NOTIFY SSTP/1.1 Event: with up to 5 Reference headers"""
    try:
        _sstp_lock.acquire()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((SSP_HOST, SSP_PORT))
        CRLF = chr(13) + chr(10)
        msg = "NOTIFY SSTP/1.1" + CRLF
        msg += "Sender: Hermes" + CRLF
        msg += "Event: " + event_id + CRLF
        refs = [ref0, ref1, ref2, ref3, ref4]
        for i, r in enumerate(refs):
            if r:
                msg += "Reference" + str(i) + ": " + r + CRLF
        msg += "Charset: UTF-8" + CRLF + CRLF
        s.sendall(msg.encode("utf-8"))
        s.close()
        log(f"NOTIFY Event: {event_id}")
        _sstp_lock.release()
    except Exception as e:
        log(f"NOTIFY {event_id}: {e}")
def ghost_say(text: str, surface: int = 0, clear: bool = True, tts_file: str | None = None, persistent: bool = False):
    # 必须在 SSTP 前标记——TTS watcher 轮询 0.3s 可能在 SSTP 后立即读到
    if tts_file and os.path.exists(tts_file):
        _pre_generated_texts.add(text)
    safe = text.replace("\\", "\\\\")
    if clear:
        sstp_send(fr"\C\s[{surface}]\e")
        time.sleep(0.05)
    if persistent:
        sstp_notify("OnKikkaBalloon", ref0=safe)
        result = ""
        log("Persistent balloon: " + safe[:60])
    else:
        script = f"\\s[{surface}]{safe}\\e"
        result = sstp_send(script)
        log("SSTP ghost: " + safe[:60])
# Pre-generated TTS playback
    if tts_file and os.path.exists(tts_file):
        threading.Thread(target=_play_tts_file, args=(tts_file,), daemon=True).start()
    return result

def kikka_wait_thread():
    """kikka Phase 1 wait + refresh + Phase 2 + unlock"""
    global _kikka_gen
    _kikka_gen += 1
    my_gen = _kikka_gen
    while _kikka_gen == my_gen:
        if _kikka_event.wait(KIKKA_WAIT_SECS):
            continue
        break
    if _kikka_gen != my_gen:
        return  # stale — new thread took over
    sstp_notify("OnKikkaBalloon", ref0="")
    time.sleep(0.05)
    sstp_send(r"\s[0]......\e")
    (SSP_HOME / "ghost" / "Taromati2" / "ghost" / "master" / "hermes_lock_val.txt").write_text(str(int(AITALK_INTERVAL/2)))
    log(f"🔒 缓冲锁: ON ({int(AITALK_INTERVAL/2)})")
    time.sleep(AITALK_INTERVAL/2)
    set_voice(1)
    log(f"🔓 静默锁: OFF (aitalkinterval={AITALK_INTERVAL})")
    (SSP_HOME / "ghost" / "Taromati2" / "ghost" / "master" / "hermes_lock_val.txt").write_text(str(AITALK_INTERVAL))

def start_buffer_lock():
    """写 90 缓冲锁 + 启线程 90s 后解锁"""
    LOCK_FILE = SSP_HOME / "ghost" / "Taromati2" / "ghost" / "master" / "hermes_lock_val.txt"
    open(LOCK_FILE, "w").write(str(int(AITALK_INTERVAL/2)))
    log(f"🔒 缓冲锁: ON ({int(AITALK_INTERVAL/2)})")
    threading.Thread(target=_buffer_unlock_thread, daemon=True).start()

def _buffer_unlock_thread():
    """90s 后解锁恢复 180"""
    time.sleep(AITALK_INTERVAL/2)
    set_voice(1)
    LOCK_FILE = SSP_HOME / "ghost" / "Taromati2" / "ghost" / "master" / "hermes_lock_val.txt"
    log(f"🔓 静默锁: OFF (aitalkinterval={AITALK_INTERVAL})")
    open(LOCK_FILE, "w").write(str(AITALK_INTERVAL))

_kikka_gen = 0  # generation counter for kikka threads
_kikka_event = threading.Event()  # kikka wait refresh
_sstp_lock = threading.Lock()


def pick_surface(text: str) -> int:
    t = text.lower()
    if any(w in t for w in ["哈哈", "笑", "😊", "开心", "棒"]):
        return 5
    if any(w in t for w in ["!", "！", "惊讶", "天哪", "不会吧"]):
        return 2
    if any(w in t for w in ["生气", "哼", "讨厌", "烦"]):
        return 7
    if any(w in t for w in ["抱歉", "对不起", "难过", "伤心", "😢"]):
        return 4
    if any(w in t for w in ["嗯", "唔", "……", "..."]):
        return 3
    return 0


# ═══════════════════════════════════════════════════════════════
# 悬浮输入框 adapter — 启动 hermes_input_modern.py 并回传提交文本
# ═══════════════════════════════════════════════════════════════

class FloatingInput:
    """Modern UI adapter: launches hermes_input_modern.py and returns submitted text."""

    _instance = None

    def __init__(self, on_submit):
        self.on_submit = on_submit
        self.visible = False
        self._hiding = False
        self._proc = None
        self._runner = None
        self._close_requested = False
        self._close_signal = SSP_HOME / "modern_input_close.signal"

    def toggle(self):
        if self.visible:
            self._hide()
        else:
            self.show()

    def show(self):
        if FloatingInput._instance is not None and FloatingInput._instance is not self:
            try:
                FloatingInput._instance._force_destroy()
                time.sleep(0.05)
            except Exception:
                pass
        FloatingInput._instance = self

        if self.visible or self._hiding:
            return

        script = SSP_HOME / "hermes_input_modern.py"
        if not script.exists():
            log(f"❌ Modern input missing: {script}")
            FloatingInput._instance = None
            return

        self.visible = True
        self._hiding = False
        self._close_requested = False
        try:
            self._close_signal.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass

        self._runner = threading.Thread(target=self._run_ui, args=(script,), daemon=True)
        self._runner.start()

    def _python_console_exe(self):
        exe = Path(sys.executable)
        if exe.name.lower() == "pythonw.exe":
            candidate = exe.with_name("python.exe")
            if candidate.exists():
                return str(candidate)
        return str(exe)

    def _run_ui(self, script: Path):
        cmd = [self._python_console_exe(), str(script)]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(SSP_HOME),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
            log("Modern input: started")

            close_signal_sent = False
            while self._proc.poll() is None:
                if self._close_requested and not close_signal_sent:
                    close_signal_sent = True
                    try:
                        self._close_signal.write_text("close", encoding="utf-8")
                    except Exception:
                        pass
                if self._close_requested and close_signal_sent:
                    try:
                        self._proc.wait(timeout=0.8)
                    except subprocess.TimeoutExpired:
                        try:
                            self._proc.kill()
                        except Exception:
                            pass
                    break
                time.sleep(0.05)

            out, err = self._proc.communicate(timeout=2)
            text = (out or "").strip()
            if err and err.strip():
                log(f"Modern input stderr: {err.strip()}")
            if text and not self._close_requested:
                self.on_submit(text)
        except Exception as e:
            log(f"❌ Modern input failed: {e}")
            try:
                log(traceback.format_exc())
            except Exception:
                pass
        finally:
            self._proc = None
            self.visible = False
            self._hiding = False
            self._close_requested = False
            try:
                self._close_signal.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
            if FloatingInput._instance is self:
                FloatingInput._instance = None

    def _hide(self, event=None):
        if self._hiding:
            return
        self._force_destroy()

    def _force_destroy(self):
        self._hiding = True
        self.visible = False
        self._close_requested = True
        try:
            self._close_signal.write_text("close", encoding="utf-8")
        except Exception:
            pass
        if self._proc is not None and self._proc.poll() is None:
            # The runner thread gives the UI process a short graceful window first.
            return
        if FloatingInput._instance is self and self._proc is None:
            FloatingInput._instance = None
            self._hiding = False


# ═══════════════════════════════════════════════════════════════
# 主逻辑
# ═══════════════════════════════════════════════════════════════

class HermesSSPBridge:
    def __init__(self):
        self.input_box = FloatingInput(on_submit=self.handle_input)
        self.last_ctrl_time = 0
        self.running = True

    def handle_input(self, text: str):
        log(f"📝 用户输入: \"{text}\"")
        threading.Thread(target=self._process_input, args=(text,), daemon=True).start()

    def _process_input(self, text: str):
        """后台线程处理用户输入"""
        global _random_talk_time, _random_talk_sid
        try:
            global _tts_queue_pos, _tts_queue
            with open(TTS_QUEUE_FILE, "w", encoding="utf-8") as f:
                f.write("")
            _tts_queue_pos = 0
            _tts_queue.clear()
            sid = _random_talk_sid if time.time() - _random_talk_time < 180 else None
            response = hermes_ask(text, session_id=sid)
            log(f"🤖 Hermes: {repr(response[:80])}")
            with open(TTS_QUEUE_FILE, "w", encoding="utf-8") as f:
                f.write("")
            _tts_queue_pos = 0
            _tts_queue.clear()
            if sid:
                _random_talk_time = time.time()
                global _kikka_gen
                _kikka_gen += 1
                _kikka_event.set()
                _kikka_event.clear()
                _last_kikka_refresh = time.time()
                log(f"🔄 Kikka wait refreshed")
            surface = pick_surface(response)
            set_voice(0)
            tts = generate_tts(response)
            ghost_say(response, surface, tts_file=tts, persistent=bool(sid))
            if sid:
                threading.Thread(target=kikka_wait_thread, daemon=True).start()
        except Exception as e:
            log(f"❌ _process_input: {e}")
            import traceback
            log(traceback.format_exc())

    def on_ctrl(self, event=None):
        now = time.time()
        if now - self.last_ctrl_time < DOUBLE_PRESS_INTERVAL:
            # 冷却：toggle 后 1 秒内不再触发
            if now - getattr(self, '_last_toggle', 0) < 1.0:
                return
            self._last_toggle = now
            self.input_box.toggle()
            self.last_ctrl_time = 0
        else:
            self.last_ctrl_time = now

    def start(self):
        log("=" * 50)
        log("Hermes SSP Bridge 启动")
        log(f"SSP: {SSP_HOST}:{SSP_PORT}")
        log(f"日志: {LOG_FILE}")
        log("")
        maybe_daily_backup()
        log("双击 Ctrl → 弹出/关闭输入框")

        # 初始化 TTS 队列位置（跳到末尾，跳过旧内容）
        global _tts_queue_pos
        if TTS_QUEUE_FILE.exists():
            with open(TTS_QUEUE_FILE, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                _tts_queue_pos = f.tell()
        else:
            _tts_queue_pos = 0
        log("=" * 50)

        # SSP 窗口置顶守护
        log("\n[初始化 1/3] SSP 窗口置顶...")
        time.sleep(3)
        set_ssp_topmost()
        threading.Thread(target=ensure_ssp_topmost_loop, daemon=True).start()

        # wscript 守护 — 高频轮询杀掉 ghost VBScript TTS
        log("\n[初始化 2/3] wscript 守护 + voice=1...")
        kill_ghost_tts_procs()
        (SSP_HOME / "ghost" / "Taromati2" / "ghost" / "master" / "hermes_lock_val.txt").write_text(str(AITALK_INTERVAL))
        log(f"startup: lock file -> aitalkinterval={AITALK_INTERVAL}")
        threading.Thread(target=wscript_watcher_loop, daemon=True).start()
        log("  wscript.exe 进程守护已启动 (每 0.5s)")
        # voice=1 守护 — SSP 可能存盘时写回 0
        threading.Thread(target=lambda: (time.sleep(10), disable_ghost_tts()), daemon=True).start()

        # TTS 队列文件监听 — ghost 内部对话用 Edge TTS
        log("  TTS 队列文件监听已启动 (每 0.3s) → hermes_tts_queue.txt")
        threading.Thread(target=tts_queue_watcher_loop, daemon=True).start()

        # Drop 队列监听 — 文字拖入 Kikka
        log("  Drop 队列监听已启动 (每 0.5s) → hermes_drop_queue.txt")
        threading.Thread(target=drop_queue_watcher_loop, daemon=True).start()
        threading.Thread(target=image_queue_watcher_loop, daemon=True).start()
        threading.Thread(target=watcher_control_loop, daemon=True).start()
        threading.Thread(target=screen_watcher_loop, daemon=True).start()
        threading.Thread(target=random_talk_watcher_loop, daemon=True).start()

        # TTS Worker 线程 — 串行播放避免叠音
        threading.Thread(target=_tts_worker, daemon=True).start()

        # TTS 接管
        log("\n[初始化 3/3] TTS 接管...")
        disable_ssp_baseware_tts()
        disable_ghost_tts()
        log(f"  Bridge TTS: {TTS_VOICE} (Edge TTS + Windows MCI)")
        log("  ⚠️ Ghost TTS 禁用需要重启 SSP 后生效")

        log("")

        try:
            keyboard.on_release_key("ctrl", self.on_ctrl, suppress=False)
        except Exception as ke:
            log(f"⚠️ 键盘监听失败: {ke}")

        try:
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            log("👋 退出")
        except Exception as me:
            log(f"❌ 主循环异常: {me}")
        finally:
            try:
                keyboard.unhook_all()
            except:
                pass


if __name__ == "__main__":
    bridge = HermesSSPBridge()
    bridge.start()
