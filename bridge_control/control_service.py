#!/usr/bin/env python3
"""Local web control service for hermes-ssp-bridge.

This service is intentionally small. It binds to 127.0.0.1 only, serves a
minimal control UI, and starts/stops the existing bridge process without
importing bridge runtime code. Hermes Gateway process inspection uses psutil
when it is available and falls back to HTTP health checks otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "bridge_workspace"
DOTENV = ROOT / ".env"
SERVICE_LOG = WORKSPACE / "control_service.log"
BRIDGE_CHILD_STDERR = WORKSPACE / "bridge_child_stderr.log"
WATCHER_CONTROL_FILE = WORKSPACE / "watcher_control.json"
CONTROL_PID_FILE = WORKSPACE / "control_service.pid"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_REQUEST_BODY = 4 * 1024 * 1024
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class RequestBodyTooLarge(ValueError):
    """Raised when a control-panel request exceeds the configured body limit."""


class LocalControlServer(ThreadingHTTPServer):
    """HTTP server whose loopback port cannot be shared by another process."""

    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


_process: subprocess.Popen[str] | None = None
_process_lock = threading.Lock()
_last_bridge_exit: dict[str, Any] | None = None
_expected_stop_pids: set[int] = set()
_theme_lock = threading.Lock()
_theme_state: dict[str, Any] = {
    "dark": False,
    "source": "default",
}
_kikka_lock = threading.Lock()
_kikka_state: dict[str, Any] = {
    "ok": False,
    "values": {},
    "message": "Not loaded yet.",
    "updated_at": None,
}
KIKKA_KEYS = [
    "Darkness",
    "Moeness",
    "Dependency",
    "Closeness",
    "Happiness",
    "kikkamood",
    "intimacy",
]
KIKKA_SET_KEYS = ["Darkness", "Moeness", "Dependency", "Closeness", "Happiness"]


def _detect_system_dark_mode() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            apps_use_light_theme, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(apps_use_light_theme) == 0
    except Exception as exc:
        _service_log(f"theme detect failed: {exc}")
        return False


def _init_theme_from_system() -> None:
    dark = _detect_system_dark_mode()
    with _theme_lock:
        _theme_state.update({"dark": dark, "source": "system"})


def _theme_status() -> dict[str, Any]:
    with _theme_lock:
        return dict(_theme_state)


def _set_theme(dark: bool) -> dict[str, Any]:
    with _theme_lock:
        _theme_state.update({"dark": bool(dark), "source": "user"})
        return dict(_theme_state)


def _load_dotenv(path: Path = DOTENV) -> None:
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as exc:
        _service_log(f"dotenv load failed: {exc}")


def _default_python() -> str:
    configured = os.environ.get("HERMES_BRIDGE_PYTHON", "").strip()
    if configured:
        return configured
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return str(exe)


def _default_gateway_python() -> str:
    configured = os.environ.get("HERMES_GATEWAY_PYTHON", "").strip()
    if configured:
        return configured
    return _default_python()


def config() -> dict[str, Any]:
    _load_dotenv()
    root = Path(os.environ.get("HERMES_SSP_ROOT", str(ROOT))).expanduser().resolve()
    port = int(os.environ.get("BRIDGE_CONTROL_PORT", "1313"))
    bridge_script = Path(
        os.environ.get("HERMES_BRIDGE_SCRIPT", str(root / "bridge_wrapper.py"))
    ).expanduser()
    if not bridge_script.is_absolute():
        bridge_script = root / bridge_script
    log_file = Path(os.environ.get("HERMES_BRIDGE_LOG", str(root / "hermes_bridge.log"))).expanduser()
    lock_file = Path(os.environ.get("HERMES_BRIDGE_LOCK", str(root / "hermes_bridge.lock"))).expanduser()
    nurturance_file = Path(
        os.environ.get("HERMES_NURTURANCE_FILE", str(root / "hermes_nurturance_val.txt"))
    ).expanduser()
    gateway_profile = os.environ.get("HERMES_GATEWAY_PROFILE", "kikka").strip() or "kikka"
    gateway_home = (
        Path(os.environ.get("HERMES_GATEWAY_HOME", "")).expanduser()
        if os.environ.get("HERMES_GATEWAY_HOME")
        else None
    )
    return {
        "root": root,
        "port": port,
        "ssp_host": os.environ.get("HERMES_SSP_HOST", "127.0.0.1"),
        "ssp_port": int(os.environ.get("HERMES_SSP_PORT", "9801")),
        "bridge_python": _default_python(),
        "bridge_script": bridge_script.resolve(),
        "log_file": log_file.resolve(),
        "lock_file": lock_file.resolve(),
        "nurturance_file": nurturance_file.resolve(),
        "gateway_host": os.environ.get("HERMES_GATEWAY_HOST", "127.0.0.1"),
        "gateway_port": int(os.environ.get("HERMES_GATEWAY_PORT", "8642")),
        "gateway_python": _default_gateway_python(),
        "gateway_module": os.environ.get("HERMES_GATEWAY_MODULE", "hermes_cli.main"),
        "gateway_profile": gateway_profile,
        "gateway_home": gateway_home.resolve() if gateway_home else None,
    }


def _service_log(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        with SERVICE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def _read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        return int(text) if text else None
    except Exception:
        return None


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                timeout=5,
            )
            return str(pid) in result.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _pid_command_line(pid: int) -> str:
    if os.name != "nt":
        return ""
    script = f'(Get-CimInstance Win32_Process -Filter "ProcessId = {int(pid)}").CommandLine'
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=8,
        )
        return (result.stdout or "").strip()
    except Exception:
        return ""


def _bridge_status() -> dict[str, Any]:
    cfg = config()
    with _process_lock:
        global _process
        proc = _process
        if proc is not None and proc.poll() is None:
            return {"running": True, "pid": proc.pid, "source": "service", "last_exit": _last_bridge_exit}
        if proc is not None:
            _process = None
    pid = _read_pid(cfg["lock_file"])
    if pid and _pid_running(pid):
        return {"running": True, "pid": pid, "source": "lock", "last_exit": _last_bridge_exit}
    return {"running": False, "pid": None, "source": "none", "last_exit": _last_bridge_exit}


def _tail(path: Path, lines: int = 240) -> str:
    try:
        if not path.exists():
            return ""
        data = path.read_bytes()[-256 * 1024 :]
        text = data.decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:])
    except Exception as exc:
        return f"Unable to read log: {exc}"


def _start_bridge() -> tuple[bool, str]:
    cfg = config()
    status = _bridge_status()
    if status["running"]:
        return True, f"Bridge is already running (PID {status['pid']})."
    if not cfg["bridge_script"].exists():
        return False, f"Bridge script not found: {cfg['bridge_script']}"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(cfg["root"]) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cmd = [str(cfg["bridge_python"]), str(cfg["bridge_script"])]
    stderr_handle = None
    try:
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        stderr_handle = BRIDGE_CHILD_STDERR.open("a", encoding="utf-8")
        stderr_handle.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] start: {' '.join(cmd)}\n")
        stderr_handle.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(cfg["root"]),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr_handle,
            text=True,
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )
        with _process_lock:
            global _process, _last_bridge_exit
            _process = proc
            _last_bridge_exit = None
        threading.Thread(target=_monitor_bridge_process, args=(proc,), daemon=True).start()
        _service_log(f"bridge start requested: pid={proc.pid}")
        return True, f"Bridge start requested (PID {proc.pid})."
    except Exception as exc:
        _service_log(f"bridge start failed: {exc}")
        return False, f"Bridge start failed: {exc}"
    finally:
        if stderr_handle is not None:
            try:
                stderr_handle.close()
            except Exception:
                pass


def _monitor_bridge_process(proc: subprocess.Popen[str]) -> None:
    code = proc.wait()
    with _process_lock:
        expected = proc.pid in _expected_stop_pids
        _expected_stop_pids.discard(proc.pid)
    exit_info = {
        "pid": proc.pid,
        "returncode": code,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stderr_log": str(BRIDGE_CHILD_STDERR),
        "expected": expected,
    }
    with _process_lock:
        global _process, _last_bridge_exit
        _last_bridge_exit = exit_info
        if _process is proc:
            _process = None
    _service_log(f"bridge process exited: pid={proc.pid} returncode={code}")


def _stop_pid(pid: int) -> tuple[bool, str]:
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                timeout=10,
            )
            if result.returncode == 0:
                return True, f"Bridge stop requested (PID {pid})."
            return False, (result.stderr or result.stdout or "taskkill failed").strip()
        except Exception as exc:
            return False, f"Bridge stop failed: {exc}"
    try:
        os.kill(pid, signal.SIGTERM)
        return True, f"Bridge stop requested (PID {pid})."
    except Exception as exc:
        return False, f"Bridge stop failed: {exc}"


def _stop_bridge() -> tuple[bool, str]:
    cfg = config()
    status = _bridge_status()
    if not status["running"] or not status["pid"]:
        return True, "Bridge is not running."

    pid = int(status["pid"])
    with _process_lock:
        launched_by_service = _process is not None and _process.pid == pid

    if not launched_by_service:
        cmdline = _pid_command_line(pid)
        lowered = cmdline.lower()
        root_text = str(cfg["root"]).lower()
        if "hermes_bridge.py" not in lowered and root_text not in lowered:
            return False, "Refusing to stop a process that does not look like this bridge."

    with _process_lock:
        _expected_stop_pids.add(pid)
    ok, message = _stop_pid(pid)
    if ok:
        _service_log(f"bridge stop requested: pid={pid}")
        time.sleep(0.5)
        if not _pid_running(pid):
            with _process_lock:
                global _last_bridge_exit
                _last_bridge_exit = None
            try:
                cfg["lock_file"].unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
    else:
        with _process_lock:
            _expected_stop_pids.discard(pid)
    return ok, message


DEFAULT_WATCHER_CONTROL = {"talk": True, "screen": True}


def _coerce_watcher_control(data: dict[str, Any] | None) -> dict[str, bool]:
    source = data if isinstance(data, dict) else {}
    return {
        "talk": bool(source.get("talk", DEFAULT_WATCHER_CONTROL["talk"])),
        "screen": bool(source.get("screen", DEFAULT_WATCHER_CONTROL["screen"])),
    }


def _watcher_control_status() -> dict[str, Any]:
    try:
        if WATCHER_CONTROL_FILE.exists():
            data = json.loads(WATCHER_CONTROL_FILE.read_text(encoding="utf-8"))
            state = _coerce_watcher_control(data)
        else:
            state = dict(DEFAULT_WATCHER_CONTROL)
        return {"ok": True, **state, "path": str(WATCHER_CONTROL_FILE)}
    except Exception as exc:
        return {"ok": False, **DEFAULT_WATCHER_CONTROL, "path": str(WATCHER_CONTROL_FILE), "message": str(exc)}


def _set_watcher_control(data: dict[str, Any]) -> dict[str, Any]:
    current = _watcher_control_status()
    state = _coerce_watcher_control({**current, **(data if isinstance(data, dict) else {})})
    try:
        WATCHER_CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True)
        WATCHER_CONTROL_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, **state, "path": str(WATCHER_CONTROL_FILE), "message": "Watcher settings saved."}
    except Exception as exc:
        return {"ok": False, **state, "path": str(WATCHER_CONTROL_FILE), "message": str(exc)}


GATEWAY_PROFILE_FILES = ["soul.md", "memory.md", "user.md", "config.yaml"]
GATEWAY_PROFILE_FILE_PATHS = {
    "soul.md": Path("soul.md"),
    "memory.md": Path("memories") / "MEMORY.md",
    "user.md": Path("memories") / "USER.md",
    "config.yaml": Path("config.yaml"),
}


def _safe_cmdline(proc: Any) -> list[str]:
    try:
        cmdline = proc.cmdline()
        return [str(part) for part in cmdline]
    except Exception:
        return []


def _safe_environ(proc: Any) -> dict[str, str]:
    try:
        env = proc.environ()
        return {str(key): str(value) for key, value in env.items()}
    except Exception:
        return {}


def _safe_connections(proc: Any) -> list[Any]:
    try:
        return proc.net_connections(kind="inet")
    except AttributeError:
        try:
            return proc.connections(kind="inet")
        except Exception:
            return []
    except Exception:
        return []


def _extract_profile_from_cmdline(cmdline: list[str]) -> str | None:
    for index, part in enumerate(cmdline):
        if part == "--profile" and index + 1 < len(cmdline):
            return cmdline[index + 1]
        if part.startswith("--profile="):
            return part.split("=", 1)[1]
    return None


def _health_check_gateway(host: str, port: int) -> dict[str, Any]:
    url = f"http://{host}:{int(port)}/health"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            raw = response.read(4096).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"raw": raw}
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "url": url,
                "payload": payload,
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": int(exc.code), "url": url, "payload": {}}
    except Exception as exc:
        return {"ok": False, "status": None, "url": url, "payload": {}, "error": str(exc)}


def _gateway_process_candidates() -> list[dict[str, Any]]:
    try:
        import psutil
    except Exception:
        return []

    cfg = config()
    candidates: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "create_time"]):
        cmdline = _safe_cmdline(proc)
        text = " ".join(cmdline).lower()
        if "gateway" not in text or "hermes" not in text:
            continue

        env = _safe_environ(proc)
        listening = []
        for conn in _safe_connections(proc):
            local = getattr(conn, "laddr", None)
            status = str(getattr(conn, "status", "")).upper()
            if not local or "LISTEN" not in status:
                continue
            host = getattr(local, "ip", None) or local[0]
            port = int(getattr(local, "port", None) or local[1])
            listening.append({"host": host, "port": port})

        profile = env.get("HERMES_PROFILE") or _extract_profile_from_cmdline(cmdline)
        home = env.get("HERMES_HOME") or ""
        has_gateway_port = any(int(item["port"]) == int(cfg["gateway_port"]) for item in listening)
        candidates.append(
            {
                "pid": int(proc.info["pid"]),
                "name": proc.info.get("name") or "",
                "create_time": proc.info.get("create_time"),
                "cmdline": cmdline,
                "profile": profile,
                "home": home,
                "listening": listening,
                "matches_port": has_gateway_port,
            }
        )
    candidates.sort(key=lambda item: (not item["matches_port"], item.get("create_time") or 0))
    return candidates


def _gateway_status() -> dict[str, Any]:
    cfg = config()
    health = _health_check_gateway(str(cfg["gateway_host"]), int(cfg["gateway_port"]))
    candidates = _gateway_process_candidates()
    selected = next((item for item in candidates if item["matches_port"]), None)
    if selected is None and candidates:
        selected = candidates[0]

    profile = (selected or {}).get("profile") or cfg["gateway_profile"]
    home = (selected or {}).get("home") or (str(cfg["gateway_home"]) if cfg["gateway_home"] else "")
    running = bool(health["ok"] or (selected and selected.get("matches_port")))
    return {
        "running": running,
        "pid": (selected or {}).get("pid"),
        "source": "process" if selected else ("health" if health["ok"] else "none"),
        "profile": profile,
        "home": home,
        "host": cfg["gateway_host"],
        "port": cfg["gateway_port"],
        "health": health,
        "version": (health.get("payload") or {}).get("version"),
        "platform": (health.get("payload") or {}).get("platform"),
        "cmdline": " ".join((selected or {}).get("cmdline") or []),
        "listening": (selected or {}).get("listening") or [],
        "files": _gateway_file_meta(home),
        "psutil": bool(candidates or selected),
    }


def _run_gateway_cli(action: str, timeout: float = 90.0) -> tuple[bool, str]:
    cfg = config()
    cmd = [
        str(cfg["gateway_python"]),
        "-m",
        str(cfg["gateway_module"]),
        "--profile",
        str(cfg["gateway_profile"]),
        "gateway",
        action,
    ]
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cfg["root"]),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=CREATE_NO_WINDOW,
            timeout=timeout,
            check=False,
        )
        output = (completed.stdout or completed.stderr or "").strip()
        _service_log(
            f"gateway {action}: exit={completed.returncode}"
            + (f" output={output}" if output else "")
        )
        if completed.returncode == 0:
            return True, output or f"Gateway {action} completed."
        return False, output or f"Gateway {action} failed (exit code {completed.returncode})."
    except subprocess.TimeoutExpired:
        message = f"Gateway {action} timed out after {int(timeout)} seconds."
        _service_log(message)
        return False, message
    except Exception as exc:
        _service_log(f"gateway {action} failed: {exc}")
        return False, f"Gateway {action} failed: {exc}"


def _wait_for_gateway_state(expected_running: bool, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest = _gateway_status()
    while bool(latest["running"]) != expected_running and time.monotonic() < deadline:
        time.sleep(0.5)
        latest = _gateway_status()
    return latest


def _start_gateway() -> tuple[bool, str]:
    status = _gateway_status()
    if status["running"]:
        pid = f"PID {status['pid']}" if status.get("pid") else "health check ok"
        return True, f"Gateway is already running ({pid})."
    ok, message = _run_gateway_cli("start")
    if not ok:
        return False, message
    latest = _wait_for_gateway_state(True)
    if latest["running"]:
        return True, message
    return False, f"{message} Gateway did not become healthy within 30 seconds."


def _stop_gateway() -> tuple[bool, str]:
    status = _gateway_status()
    if not status["running"]:
        return True, "Gateway is not running."
    ok, message = _run_gateway_cli("stop")
    if not ok:
        return False, message
    latest = _wait_for_gateway_state(False)
    if not latest["running"]:
        return True, message
    return False, f"{message} Gateway is still healthy after 30 seconds."


def _restart_gateway() -> tuple[bool, str]:
    ok, message = _run_gateway_cli("restart")
    if not ok:
        return False, message
    latest = _wait_for_gateway_state(True)
    if latest["running"]:
        return True, message
    return False, f"{message} Gateway did not become healthy within 30 seconds."


def _gateway_file_meta(home: str) -> dict[str, Any]:
    if not home:
        return {"available": False, "message": "Gateway HERMES_HOME not detected.", "items": {}}
    root = Path(home).expanduser()
    items = {}
    for name in GATEWAY_PROFILE_FILES:
        path = root / GATEWAY_PROFILE_FILE_PATHS[name]
        try:
            exists = path.exists()
            size = path.stat().st_size if exists else 0
            error = ""
        except Exception as exc:
            exists = False
            size = 0
            error = str(exc)
        items[name] = {
            "exists": exists,
            "path": str(path),
            "size": size,
            "error": error,
        }
    return {"available": True, "home": str(root), "items": items}


def _resolve_gateway_file(home: str, name: str) -> Path:
    if name not in GATEWAY_PROFILE_FILES:
        raise ValueError("Unsupported profile file.")
    if not home:
        raise ValueError("Gateway HERMES_HOME not detected.")
    root = Path(home).expanduser().resolve()
    path = (root / GATEWAY_PROFILE_FILE_PATHS[name]).resolve()
    if root != path and root not in path.parents:
        raise ValueError("Profile file path escaped HERMES_HOME.")
    return path


def _read_gateway_files() -> dict[str, Any]:
    status = _gateway_status()
    home = str(status.get("home") or "")
    files = {}
    for name in GATEWAY_PROFILE_FILES:
        try:
            path = _resolve_gateway_file(home, name)
            files[name] = {
                "ok": True,
                "exists": path.exists(),
                "text": path.read_text(encoding="utf-8", errors="replace") if path.exists() else "",
            }
        except Exception as exc:
            files[name] = {"ok": False, "exists": False, "text": "", "message": str(exc)}
    return {"ok": bool(home), "gateway": status, "files": files}


def _save_gateway_files(files: dict[str, Any]) -> dict[str, Any]:
    status = _gateway_status()
    home = str(status.get("home") or "")
    saved = {}
    try:
        if not home:
            raise ValueError("Gateway HERMES_HOME not detected.")
        for name, text in files.items():
            path = _resolve_gateway_file(home, name)
            new_text = str(text)
            path.parent.mkdir(parents=True, exist_ok=True)
            backup = None
            if path.exists() and path.read_text(encoding="utf-8", errors="replace") != new_text:
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                backup = path.with_name(f"{path.name}.web-backup.{timestamp}")
                shutil.copy2(path, backup)
            path.write_text(new_text, encoding="utf-8", newline="\n")
            saved[name] = {
                "ok": True,
                "path": str(path),
                "size": path.stat().st_size,
                "backup": str(backup) if backup else "",
            }
        return {"ok": True, "message": "Gateway profile files saved.", "gateway": _gateway_status(), "saved": saved}
    except Exception as exc:
        return {"ok": False, "message": str(exc), "gateway": status, "saved": saved}


def _api_status() -> dict[str, Any]:
    cfg = config()
    status = _bridge_status()
    return {
        "service": {
            "host": "127.0.0.1",
            "port": cfg["port"],
            "root": str(cfg["root"]),
        },
        "bridge": {
            **status,
            "script": str(cfg["bridge_script"]),
            "log_file": str(cfg["log_file"]),
            "lock_file": str(cfg["lock_file"]),
        },
        "log": _tail(cfg["log_file"]),
        "kikka": _kikka_status(),
        "gateway": _gateway_status(),
        "watchers": _watcher_control_status(),
        "theme": _theme_status(),
    }


def _clear_log() -> tuple[bool, str]:
    cfg = config()
    try:
        cfg["log_file"].parent.mkdir(parents=True, exist_ok=True)
        cfg["log_file"].write_text("", encoding="utf-8")
        _service_log(f"bridge log cleared: {cfg['log_file']}")
        return True, "Bridge log cleared."
    except Exception as exc:
        _service_log(f"bridge log clear failed: {exc}")
        return False, f"Bridge log clear failed: {exc}"


def _parse_kikka_vars(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for raw in text.replace(",", "\n").splitlines():
        line = raw.strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key in KIKKA_KEYS and value.isdigit():
            values[key] = int(value)
    return values


def _refresh_kikka_vars() -> dict[str, Any]:
    cfg = config()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        body = (
            "NOTIFY SSTP/1.1\r\n"
            "Sender: Hermes\r\n"
            "Event: OnGetNurturance\r\n"
            "Charset: UTF-8\r\n\r\n"
        )
        try:
            cfg["nurturance_file"].unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass

        with socket.create_connection((cfg["ssp_host"], int(cfg["ssp_port"])), timeout=2) as client:
            client.sendall(body.encode("utf-8"))

        for _ in range(20):
            if cfg["nurturance_file"].exists():
                break
            time.sleep(0.05)

        if not cfg["nurturance_file"].exists():
            raise RuntimeError(f"Nurturance file not written: {cfg['nurturance_file']}")

        text = cfg["nurturance_file"].read_text(encoding="utf-8", errors="replace")
        values = _parse_kikka_vars(text)
        missing = [key for key in KIKKA_KEYS if key not in values]
        if missing:
            raise RuntimeError("Missing values: " + ", ".join(missing))

        state = {
            "ok": True,
            "values": values,
            "message": "Kikka variables refreshed.",
            "updated_at": stamp,
        }
    except Exception as exc:
        state = {
            "ok": False,
            "values": {},
            "message": str(exc),
            "updated_at": stamp,
        }

    with _kikka_lock:
        _kikka_state.update(state)
        result = dict(_kikka_state)
    _service_log(f"kikka vars refresh: ok={result['ok']} message={result['message']}")
    return result


def _set_kikka_vars(values: dict[str, Any]) -> dict[str, Any]:
    cfg = config()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        refs: list[int] = []
        for key in KIKKA_SET_KEYS:
            raw = values.get(key)
            value = int(raw)
            if value < 0 or value > 100:
                raise ValueError(f"{key} must be between 0 and 100")
            refs.append(value)

        lines = [
            "NOTIFY SSTP/1.1",
            "Sender: Hermes",
            "Event: OnSetNurturance",
        ]
        for index, value in enumerate(refs):
            lines.append(f"Reference{index}: {value}")
        lines.append("Charset: UTF-8")
        body = "\r\n".join(lines) + "\r\n\r\n"

        with socket.create_connection((cfg["ssp_host"], int(cfg["ssp_port"])), timeout=2) as client:
            client.sendall(body.encode("utf-8"))

        state = {
            "ok": True,
            "values": {key: refs[index] for index, key in enumerate(KIKKA_SET_KEYS)},
            "message": "Kikka variables set.",
            "updated_at": stamp,
        }
    except Exception as exc:
        state = {
            "ok": False,
            "values": {},
            "message": str(exc),
            "updated_at": stamp,
        }

    with _kikka_lock:
        if state["ok"]:
            current_values = dict(_kikka_state.get("values") or {})
            current_values.update(state["values"])
            state["values"] = current_values
        _kikka_state.update(state)
        result = dict(_kikka_state)
    _service_log(f"kikka vars set: ok={result['ok']} message={result['message']}")
    return result


def _kikka_status() -> dict[str, Any]:
    with _kikka_lock:
        return dict(_kikka_state)


def build_check_report() -> dict[str, Any]:
    cfg = config()
    return {
        "ok": True,
        "host": "127.0.0.1",
        "port": cfg["port"],
        "root": str(cfg["root"]),
        "bridge_python": str(cfg["bridge_python"]),
        "bridge_script_exists": cfg["bridge_script"].exists(),
        "log_file": str(cfg["log_file"]),
        "lock_file": str(cfg["lock_file"]),
        "gateway_host": str(cfg["gateway_host"]),
        "gateway_port": cfg["gateway_port"],
        "gateway_profile": str(cfg["gateway_profile"]),
    }


INDEX_HTML = r"""<!doctype html>
<html lang="en" data-theme="__INITIAL_THEME__" data-loading="true">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hermes SSP Bridge</title>
  <link id="favicon" rel="icon" href="">
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --ink: #1f2933;
      --muted: #6b7280;
      --line: #d8dee8;
      --green: #178a5b;
      --green-soft: #e7f6ef;
      --red: #b8323a;
      --red-soft: #fdecee;
      --blue: #2764c4;
      --shadow: 0 16px 44px rgba(15, 23, 42, 0.10);
      --log-bg: #111827;
      --log-ink: #d1d5db;
      --slider-empty: #c8d0da;
      --slider-height: 15px;
    }
    :root[data-theme="dark"] {
      color-scheme: dark;
      --bg: #111418;
      --panel: #181d24;
      --ink: #e6edf3;
      --muted: #9aa6b2;
      --line: #303946;
      --green: #39b97e;
      --green-soft: #123625;
      --red: #f06f7a;
      --red-soft: #3d1f24;
      --blue: #7aa8ff;
      --shadow: 0 16px 44px rgba(0, 0, 0, 0.34);
      --log-bg: #0b1018;
      --log-ink: #d7dee8;
      --slider-empty: #303946;
      --slider-height: 14px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 80vh;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.5 "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    }
    main {
      width: min(1520px, calc(100vw - 56px));
      margin: 13px auto;
    }
    :root[data-loading="true"] main,
    :root[data-loading="true"] .page-toolbar {
      opacity: 0;
      pointer-events: none;
    }
    .loading-screen {
      position: fixed;
      inset: 0;
      z-index: 100;
      display: grid;
      place-items: center;
      background: var(--bg);
      color: var(--ink);
      transition: opacity 180ms ease, visibility 180ms ease;
    }
    :root:not([data-loading="true"]) .loading-screen {
      opacity: 0;
      visibility: hidden;
      pointer-events: none;
    }
    .loading-spinner {
      width: 42px;
      height: 42px;
      border-radius: 50%;
      border: 3px solid var(--line);
      border-top-color: var(--blue);
      animation: spin 850ms linear infinite;
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }
    .page-toolbar {
      position: fixed;
      top: 16px;
      right: 18px;
      z-index: 20;
      display: flex;
      justify-content: flex-end;
      align-items: center;
    }
    .app-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(360px, 1fr);
      gap: 28px;
      align-items: stretch;
    }
    .workspace-column {
      display: flex;
      flex-direction: column;
      min-width: 0;
      min-height: 0;
    }
    .column-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }
    h1 {
      margin: 0;
      font-size: 24px;
      font-weight: 650;
      letter-spacing: 0;
    }
    .subtle { color: var(--muted); font-size: 13px; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .controls {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 16px;
      padding: 18px;
      align-items: center;
      justify-content: flex-start;
      min-height: 80px;
    }
    .status-switch {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      width: fit-content;
      min-height: 42px;
      padding: 5px 7px 5px 11px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #f9fafb;
      font-weight: 600;
      line-height: 1;
      cursor: pointer;
      user-select: none;
    }
    .control-info {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      align-items: center;
      justify-content: stretch;
      gap: 10px 18px;
      min-width: 0;
      min-height: 42px;
      text-align: right;
      font-size: 13px;
    }
    .status-meta {
      line-height: 32px;
    }
    .dot {
      width: 9px;
      height: 9px;
      flex: 0 0 9px;
      border-radius: 50%;
      background: var(--muted);
    }
    #statusText {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
    }
    .running { color: var(--green); background: var(--green-soft); border-color: #b9e4cf; }
    .running .dot { background: var(--green); }
    .stopped { color: var(--red); background: var(--red-soft); border-color: #f2bdc2; }
    .stopped .dot { background: var(--red); }
    button {
      min-width: 104px;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      font-weight: 650;
      cursor: pointer;
    }
    button.primary {
      border-color: #8fcdb1;
      background: var(--green);
      color: #fff;
    }
    button.danger {
      border-color: #e3a1a7;
      background: var(--red);
      color: #fff;
    }
    button.secondary {
      min-width: 76px;
      min-height: 32px;
      padding: 0 12px;
      font-size: 13px;
      color: var(--muted);
      background: var(--panel);
    }
    button.icon-button {
      display: inline-grid;
      place-items: center;
      min-width: 32px;
      width: 32px;
      min-height: 32px;
      height: 32px;
      padding: 0;
      font-size: 17px;
      line-height: 1;
      color: var(--muted);
      background: var(--panel);
    }
    button.icon-button svg {
      width: 17px;
      height: 17px;
      display: block;
      stroke: currentColor;
    }
    #closeBridgeSettingsBtn {
      position: relative;
      font-size: 0;
    }
    #closeBridgeSettingsBtn::before,
    #closeBridgeSettingsBtn::after {
      content: "";
      position: absolute;
      left: 50%;
      top: 50%;
      width: 11px;
      height: 1.5px;
      border-radius: 999px;
      background: currentColor;
      transform-origin: center;
    }
    #closeBridgeSettingsBtn::before {
      transform: translate(-50%, -50%) rotate(45deg);
    }
    #closeBridgeSettingsBtn::after {
      transform: translate(-50%, -50%) rotate(-45deg);
    }
    button:disabled {
      opacity: 0.55;
      cursor: wait;
    }
    .message {
      min-width: 0;
      justify-self: end;
      line-height: 32px;
      color: var(--muted);
    }
    .kikka-panel {
      padding: 18px;
    }
    .kikka-panel.unlock-pulse {
      animation: kikkaUnlockPulse 1450ms cubic-bezier(0.22, 0.74, 0.28, 1);
      will-change: transform, box-shadow, background, border-color;
      transform-origin: 52% 48%;
    }
    @keyframes kikkaUnlockPulse {
      0% {
        border-color: rgba(220, 0, 32, 0.70);
        box-shadow:
          0 0 10px 0 rgba(220, 0, 32, 0.20),
          0 0 28px 3px rgba(220, 0, 32, 0.12),
          0 0 56px 10px rgba(220, 0, 32, 0.05),
          var(--shadow);
        background: var(--panel);
        transform: translate3d(0, 0, 0) rotate(0deg);
      }
      7% {
        transform: translate3d(-1.4px, 1.2px, 0) rotate(-0.28deg);
      }
      15% {
        transform: translate3d(1.6px, -1px, 0) rotate(0.34deg);
      }
      24% {
        transform: translate3d(-0.9px, -1.1px, 0) rotate(-0.18deg);
      }
      34% {
        transform: translate3d(0.7px, 0.9px, 0) rotate(0.12deg);
      }
      48% {
        border-color: rgba(220, 0, 32, 0.46);
        box-shadow:
          0 0 12px 1px rgba(220, 0, 32, 0.16),
          0 0 34px 7px rgba(220, 0, 32, 0.10),
          0 0 68px 14px rgba(220, 0, 32, 0.04),
          var(--shadow);
        transform: translate3d(-0.3px, 0.4px, 0) rotate(-0.05deg);
      }
      68% {
        border-color: rgba(220, 0, 32, 0.28);
        box-shadow:
          0 0 8px 0 rgba(220, 0, 32, 0.09),
          0 0 24px 5px rgba(220, 0, 32, 0.055),
          0 0 48px 10px rgba(220, 0, 32, 0.025),
          var(--shadow);
        transform: translate3d(0.15px, -0.25px, 0) rotate(0.02deg);
      }
      84% {
        border-color: rgba(220, 0, 32, 0.14);
        box-shadow:
          0 0 5px 0 rgba(220, 0, 32, 0.04),
          0 0 14px 2px rgba(220, 0, 32, 0.025),
          0 0 28px 5px rgba(220, 0, 32, 0.012),
          var(--shadow);
        transform: translate3d(0.04px, 0.06px, 0) rotate(0deg);
      }
      100% {
        border-color: var(--line);
        box-shadow: var(--shadow);
        background: var(--panel);
        transform: translate3d(0, 0, 0) rotate(0deg);
      }
    }
    .kikka-list {
      display: grid;
      grid-template-columns: 1fr minmax(140px, 260px) auto;
      gap: 7px 18px;
      margin: 0;
      align-items: center;
    }
    .kikka-list.editing {
      grid-template-columns: 1fr minmax(140px, 260px) auto;
    }
    .kikka-list dt {
      color: var(--muted);
    }
    .kikka-list dd {
      margin: 0;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      text-align: right;
    }
    .slider-shell {
      --slider-pos: 0%;
      --thumb-color: #FF0000;
      --slider-gradient: linear-gradient(90deg, #FF0000 0%, #660000 100%);
      position: relative;
      width: 100%;
      height: var(--slider-height);
      border-radius: 999px;
      background: var(--slider-empty);
      overflow: hidden;
    }
    .slider-shell::before {
      content: "";
      position: absolute;
      inset: 0;
      background: var(--slider-gradient);
      clip-path: inset(0 calc(100% - var(--slider-pos)) 0 0 round 999px);
      pointer-events: none;
    }
    .kikka-slider {
      position: absolute;
      inset: 0;
      width: 100%;
      height: var(--slider-height);
      margin: 0;
      appearance: none;
      background: transparent;
      cursor: pointer;
    }
    .kikka-slider:disabled {
      cursor: default;
    }
    .kikka-slider::-webkit-slider-runnable-track {
      height: var(--slider-height);
      background: transparent;
      border: 0;
    }
    .kikka-slider::-webkit-slider-thumb {
      appearance: none;
      width: 0;
      height: var(--slider-height);
      border: 0;
      border-radius: 50%;
      background: transparent;
      box-shadow: none;
    }
    .kikka-slider::-moz-range-track {
      height: var(--slider-height);
      background: transparent;
      border: 0;
    }
    .kikka-slider::-moz-range-progress {
      background: transparent;
    }
    .kikka-slider::-moz-range-thumb {
      width: 0;
      height: var(--slider-height);
      border: 0;
      border-radius: 50%;
      background: transparent;
      box-shadow: none;
    }
    .slider-placeholder {
      min-width: 140px;
    }
    .kikka-icon-row {
      display: flex;
      align-items: center;
      justify-content: flex-start;
      gap: 2px;
      min-width: 140px;
      min-height: 18px;
    }
    .kikka-stat-icon {
      width: 18px;
      height: 18px;
      object-fit: contain;
      image-rendering: pixelated;
      flex: 0 0 auto;
    }
    .kikka-note {
      margin-top: 10px;
      min-height: 20px;
      color: var(--muted);
      font-size: 13px;
    }
    .kikka-actions {
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      margin-top: 14px;
    }
    .unlock-fade-in {
      animation: unlockFadeIn 420ms ease-out;
    }
    @keyframes unlockFadeIn {
      from {
        opacity: 0;
        transform: translateY(3px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }
    .gateway-heading {
      margin-top: 18px;
      margin-bottom: 18px;
    }
    .gateway-panel {
      margin-top: 16px;
      padding: 18px;
    }
    .title-meta {
      color: var(--muted);
      font-weight: 500;
    }
    .title-meta em {
      font-style: italic;
    }
    .editor-tabs {
      display: flex;
      gap: 8px;
      margin-top: 0;
    }
    .editor-tabs button {
      flex: 0 0 auto;
      min-width: 78px;
    }
    .editor-tabs button.active {
      border-color: var(--blue);
      color: var(--blue);
    }
    .gateway-editor {
      width: 100%;
      min-height: 250px;
      margin-top: 18px;
      padding: 12px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--log-bg);
      color: var(--log-ink);
      font: 12px/1.45 Consolas, "Cascadia Mono", monospace;
    }
    .gateway-editor:disabled {
      opacity: 0.72;
      cursor: default;
    }
    .gateway-actions {
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      margin-top: 10px;
    }
    .hidden {
      display: none !important;
    }
    .settings-overlay {
      position: fixed;
      inset: 0;
      z-index: 50;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 18px;
      background: rgba(15, 23, 42, 0.20);
    }
    .settings-panel {
      width: min(360px, calc(100vw - 36px));
      padding: 18px;
      outline: none;
    }
    .settings-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }
    .settings-head h2 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }
    .watcher-setting {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 0;
      border-top: 1px solid var(--line);
    }
    .watcher-setting:first-of-type {
      border-top: 0;
    }
    .status-switch input,
    .watcher-switch input {
      position: absolute;
      opacity: 0;
      pointer-events: none;
    }
    .watcher-switch {
      display: inline-flex;
      align-items: center;
      width: fit-content;
      cursor: pointer;
      user-select: none;
    }
    .switch-track {
      position: relative;
      display: inline-block;
      width: 50px;
      height: 28px;
      flex: 0 0 auto;
      border-radius: 999px;
      background: #c8d0da;
      transition: background 160ms ease, opacity 160ms ease;
      box-shadow: inset 0 0 0 1px rgba(15, 23, 42, 0.08);
    }
    .switch-track::before {
      content: "";
      position: absolute;
      width: 22px;
      height: 22px;
      left: 3px;
      top: 3px;
      border-radius: 50%;
      background: #fff;
      box-shadow: 0 2px 8px rgba(15, 23, 42, 0.22);
      transition: transform 160ms ease;
    }
    .status-switch.running .switch-track {
      background: var(--green);
    }
    .status-switch.running .switch-track::before {
      transform: translateX(22px);
    }
    .watcher-switch.running .switch-track {
      background: var(--green);
    }
    .watcher-switch.running .switch-track::before {
      transform: translateX(22px);
    }
    .status-switch.disabled,
    .watcher-switch.disabled {
      cursor: wait;
      opacity: 0.6;
    }
    .theme-toggle {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 38px;
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--panel);
      color: var(--ink);
      box-shadow: var(--shadow);
      cursor: pointer;
      user-select: none;
    }
    .theme-toggle input {
      position: absolute;
      opacity: 0;
      pointer-events: none;
    }
    .theme-icon {
      display: none;
      width: 20px;
      height: 20px;
    }
    .theme-icon svg {
      display: block;
      width: 20px;
      height: 20px;
      stroke: currentColor;
    }
    :root:not([data-theme="dark"]) .theme-sun {
      display: block;
    }
    :root[data-theme="dark"] .theme-moon {
      display: block;
    }
    .log-panel {
      display: flex;
      flex: 1 1 auto;
      flex-direction: column;
      margin-top: 16px;
      min-height: 0;
      overflow: hidden;
    }
    .log-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 13px 16px;
      border-bottom: 1px solid var(--line);
    }
    .log-title { font-weight: 650; }
    pre {
      flex: 1 1 0;
      margin: 0;
      min-height: 420px;
      overflow: auto;
      padding: 16px;
      background: var(--log-bg);
      color: var(--log-ink);
      font: 12px/1.45 Consolas, "Cascadia Mono", monospace;
      white-space: pre-wrap;
      word-break: break-word;
    }
    @media (max-width: 720px) {
      main {
        width: min(100vw - 32px, 720px);
      }
      .app-grid {
        grid-template-columns: 1fr;
        align-items: start;
      }
      .workspace-column { display: block; }
      .log-panel { display: block; }
      .column-header { align-items: center; }
      .controls {
        grid-template-columns: 1fr;
      }
      .message {
        text-align: left;
        justify-self: start;
      }
      .control-info {
        grid-template-columns: minmax(0, 1fr) auto auto;
        justify-content: stretch;
        text-align: left;
      }
      .column-header { display: block; }
      button { flex: 1; min-width: 0; }
      pre { min-height: 360px; }
    }
  </style>
</head>
<body>
  <div class="loading-screen" id="loadingScreen" aria-hidden="true">
    <div class="loading-spinner"></div>
  </div>
  <main>
    <div class="page-toolbar">
      <label class="theme-toggle" for="themeToggle" title="Toggle color mode">
        <input id="themeToggle" type="checkbox" aria-label="Toggle night mode">
        <span class="theme-icon theme-sun" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="4"></circle>
            <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"></path>
          </svg>
        </span>
        <span class="theme-icon theme-moon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
          </svg>
        </span>
      </label>
    </div>

    <div class="app-grid">
      <section class="workspace-column">
        <header class="column-header">
          <h1>Hermes SSP Bridge</h1>
        </header>

      <section class="panel controls">
        <label class="status-switch stopped" id="statusSwitch" for="bridgeToggle">
          <input id="bridgeToggle" type="checkbox" aria-label="Toggle bridge">
          <span class="dot"></span>
          <span id="statusText">Checking</span>
          <span class="switch-track"></span>
        </label>
        <div class="control-info">
          <span class="message" id="message"></span>
          <span class="subtle status-meta" id="pidText"></span>
          <button class="icon-button" id="bridgeSettingsBtn" type="button" title="Bridge settings" aria-label="Bridge settings">
            <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M12 15.5A3.5 3.5 0 1 0 12 8.5a3.5 3.5 0 0 0 0 7Z"></path>
              <path d="M19.4 15a1.8 1.8 0 0 0 .36 1.98l.05.05a2.1 2.1 0 1 1-2.97 2.97l-.05-.05a1.8 1.8 0 0 0-1.98-.36 1.8 1.8 0 0 0-1.08 1.65V21.3a2.1 2.1 0 1 1-4.2 0v-.06a1.8 1.8 0 0 0-1.08-1.65 1.8 1.8 0 0 0-1.98.36l-.05.05a2.1 2.1 0 1 1-2.97-2.97l.05-.05A1.8 1.8 0 0 0 4.6 15a1.8 1.8 0 0 0-1.65-1.08H2.9a2.1 2.1 0 1 1 0-4.2h.06A1.8 1.8 0 0 0 4.6 8.64a1.8 1.8 0 0 0-.36-1.98l-.05-.05a2.1 2.1 0 1 1 2.97-2.97l.05.05a1.8 1.8 0 0 0 1.98.36A1.8 1.8 0 0 0 10.27 2.4V2.1a2.1 2.1 0 1 1 4.2 0v.3a1.8 1.8 0 0 0 1.08 1.65 1.8 1.8 0 0 0 1.98-.36l.05-.05a2.1 2.1 0 1 1 2.97 2.97l-.05.05a1.8 1.8 0 0 0-.36 1.98 1.8 1.8 0 0 0 1.65 1.08h.3a2.1 2.1 0 1 1 0 4.2h-.3A1.8 1.8 0 0 0 19.4 15Z"></path>
            </svg>
          </button>
        </div>
      </section>

        <section class="panel log-panel">
          <div class="log-head">
            <div class="log-title">Bridge Log</div>
            <button class="secondary" id="clearLogBtn" type="button">Clear</button>
          </div>
          <pre id="logText"></pre>
        </section>
      </section>

      <aside class="workspace-column">
        <header class="column-header">
          <h1>Kikka 变量</h1>
        </header>

        <section class="panel kikka-panel" id="kikkaPanel">
        <dl class="kikka-list" id="kikkaList"></dl>
        <div class="kikka-note" id="kikkaMessage"></div>
        <div class="kikka-actions">
          <button class="secondary hidden" id="setKikkaBtn" type="button">Set</button>
          <button class="secondary" id="refreshKikkaBtn" type="button">Refresh</button>
        </div>
        </section>

        <header class="column-header gateway-heading">
          <h1>Hermes Gateway <span class="title-meta">(<em id="gatewayTitleProfile">--</em>)</span></h1>
        </header>

        <section class="panel controls">
            <label class="status-switch stopped" id="gatewayStatusSwitch" for="gatewayToggle">
              <input id="gatewayToggle" type="checkbox" aria-label="Toggle gateway">
              <span class="dot"></span>
              <span id="gatewayStatusText">Checking</span>
              <span class="switch-track"></span>
            </label>
            <div class="control-info">
              <span class="message" id="gatewayMessage"></span>
              <span class="subtle status-meta" id="gatewayPidText"></span>
              <button class="secondary" id="gatewayRestartBtn" type="button">Restart</button>
            </div>
        </section>
        <section class="panel gateway-panel">
          <div class="editor-tabs" id="gatewayTabs">
            <button class="secondary active" type="button" data-file="soul.md">soul.md</button>
            <button class="secondary" type="button" data-file="memory.md">MEMORY.md</button>
            <button class="secondary" type="button" data-file="user.md">USER.md</button>
            <button class="secondary" type="button" data-file="config.yaml">config.yaml</button>
          </div>
          <textarea class="gateway-editor" id="gatewayEditor" spellcheck="false" disabled></textarea>
          <div class="gateway-actions">
            <button class="secondary" id="reloadGatewayFilesBtn" type="button">Reload</button>
            <button class="secondary" id="saveGatewayFilesBtn" type="button">Save</button>
          </div>
        </section>
      </aside>
    </div>

  </main>
  <div class="settings-overlay hidden" id="bridgeSettingsOverlay">
    <section class="panel settings-panel" id="bridgeSettingsPanel" tabindex="-1" role="dialog" aria-modal="true" aria-labelledby="bridgeSettingsTitle">
      <div class="settings-head">
        <h2 id="bridgeSettingsTitle">Bridge Settings</h2>
        <button class="icon-button" id="closeBridgeSettingsBtn" type="button" title="Close settings" aria-label="Close settings">×</button>
      </div>
      <div class="watcher-setting">
        <span>Kikka 随机谈话</span>
        <label class="watcher-switch running" id="talkWatcherSwitch" for="talkWatcherToggle">
          <input id="talkWatcherToggle" type="checkbox" aria-label="Toggle Kikka talk watcher">
          <span class="switch-track"></span>
        </label>
      </div>
      <div class="watcher-setting">
        <span>Kikka 屏幕回应</span>
        <label class="watcher-switch running" id="screenWatcherSwitch" for="screenWatcherToggle">
          <input id="screenWatcherToggle" type="checkbox" aria-label="Toggle Kikka screen watcher">
          <span class="switch-track"></span>
        </label>
      </div>
    </section>
  </div>

  <script>
    const statusSwitch = document.getElementById("statusSwitch");
    const pidText = document.getElementById("pidText");
    const message = document.getElementById("message");
    const logText = document.getElementById("logText");
    const bridgeToggle = document.getElementById("bridgeToggle");
    const statusText = document.getElementById("statusText");
    const clearLogBtn = document.getElementById("clearLogBtn");
    const bridgeSettingsBtn = document.getElementById("bridgeSettingsBtn");
    const bridgeSettingsOverlay = document.getElementById("bridgeSettingsOverlay");
    const bridgeSettingsPanel = document.getElementById("bridgeSettingsPanel");
    const closeBridgeSettingsBtn = document.getElementById("closeBridgeSettingsBtn");
    const talkWatcherSwitch = document.getElementById("talkWatcherSwitch");
    const talkWatcherToggle = document.getElementById("talkWatcherToggle");
    const screenWatcherSwitch = document.getElementById("screenWatcherSwitch");
    const screenWatcherToggle = document.getElementById("screenWatcherToggle");
    const themeToggle = document.getElementById("themeToggle");
    const setKikkaBtn = document.getElementById("setKikkaBtn");
    const refreshKikkaBtn = document.getElementById("refreshKikkaBtn");
    const kikkaList = document.getElementById("kikkaList");
    const kikkaMessage = document.getElementById("kikkaMessage");
    const kikkaPanel = document.getElementById("kikkaPanel");
    const gatewayStatusSwitch = document.getElementById("gatewayStatusSwitch");
    const gatewayToggle = document.getElementById("gatewayToggle");
    const gatewayStatusText = document.getElementById("gatewayStatusText");
    const gatewayPidText = document.getElementById("gatewayPidText");
    const gatewayRestartBtn = document.getElementById("gatewayRestartBtn");
    const gatewayMessage = document.getElementById("gatewayMessage");
    const gatewayTitleProfile = document.getElementById("gatewayTitleProfile");
    const gatewayTabs = document.getElementById("gatewayTabs");
    const gatewayEditor = document.getElementById("gatewayEditor");
    const reloadGatewayFilesBtn = document.getElementById("reloadGatewayFilesBtn");
    const saveGatewayFilesBtn = document.getElementById("saveGatewayFilesBtn");
    const favicon = document.getElementById("favicon");
    let busy = false;
    let watcherBusy = false;
    let gatewayBusy = false;
    let gatewayFiles = {};
    let currentGatewayFile = "soul.md";
    let gatewayFilesHome = "";
    let gatewayEditorDirty = false;
    let gatewayPendingRunning = null;
    let loadingDone = false;
    const loadingTimeout = window.setTimeout(finishLoading, 30000);
    const bridgeActionCooldownMs = 1200;
    const gatewayActionTimeoutMs = 35000;
    const gatewayActionPollMs = 500;
    const logAutoScrollThreshold = 24;
    const kikkaKeys = ["Moeness", "Darkness", "Dependency", "Closeness", "Happiness", "kikkamood", "intimacy"];
    const kikkaSetKeys = ["Darkness", "Moeness", "Dependency", "Closeness", "Happiness"];
    const kikkaLabels = {
      Moeness: "Moeness (萌度)",
      Darkness: "Darkness (腹黑度)",
      Dependency: "Dependency (依赖度)",
      Closeness: "Closeness (亲密度)",
      Happiness: "Happiness (幸福度)",
      kikkamood: "kikkamood (心情)",
      intimacy: "intimacy (好感度)"
    };
    const kikkaIconSources = {
      kikkamood: ["data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23d98b3f' stroke='%235b3218' stroke-width='1.5' d='M7 4c4-3 10 0 10 5 0 3-2 5-5 6l-2 5H5l2-6c-4-2-4-7 0-10Z'/%3E%3Cpath fill='%23f5d6a0' d='M6 5c3-2 7-1 8 2-3-1-5 0-7 2-2 0-3-2-1-4Z'/%3E%3C/svg%3E"],
      intimacy: ["data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23e84a5f' stroke='%238f1d2c' stroke-width='1.5' d='M12 21S3 15.5 3 8.8C3 4.2 8.6 2.4 12 6c3.4-3.6 9-1.8 9 2.8C21 15.5 12 21 12 21Z'/%3E%3C/svg%3E"]
    };
    const kikkaIconUrls = {};
    const konamiCode = ["ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown", "ArrowLeft", "ArrowRight", "ArrowLeft", "ArrowRight", "KeyB", "KeyA"];
    let konamiIndex = 0;
    let kikkaEditUnlocked = false;
    let lastKikka = null;
    let pendingKikkaValues = {};
    let kikkaNoticeUntil = 0;
    const kikkaIconRows = {};

    function updateFavicon(dark) {
      const stroke = dark ? "#d7dee8" : "#1f2933";
      const fill = dark ? "#111418" : "#f7f8fa";
      const icon = dark
        ? `<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>`
        : `<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>`;
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="${fill}" stroke="${stroke}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${icon}</svg>`;
      favicon.href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
    }

    function finishLoading() {
      if (loadingDone) {
        return;
      }
      loadingDone = true;
      window.clearTimeout(loadingTimeout);
      document.documentElement.dataset.loading = "false";
    }

    function loadImageSource(url) {
      return new Promise((resolve, reject) => {
        const image = new Image();
        image.decoding = "async";
        image.onload = () => resolve(url);
        image.onerror = () => reject(new Error(`Unable to load ${url}`));
        image.src = url;
      });
    }

    async function loadFirstImageSource(urls) {
      for (const url of urls) {
        try {
          return await loadImageSource(url);
        } catch (error) {
          // Try the next configured mirror.
        }
      }
      return "";
    }

    async function preloadKikkaIcons() {
      const entries = await Promise.all(
        Object.entries(kikkaIconSources).map(async ([key, urls]) => {
          return [key, await loadFirstImageSource(urls)];
        })
      );
      for (const [key, url] of entries) {
        kikkaIconUrls[key] = url;
      }
      if (lastKikka) {
        renderKikka(lastKikka);
      }
    }

    function applyTheme(theme) {
      const dark = Boolean(theme && theme.dark);
      document.documentElement.dataset.theme = dark ? "dark" : "light";
      themeToggle.checked = dark;
      themeToggle.title = dark ? "Switch to light mode" : "Switch to dark mode";
      updateFavicon(dark);
    }

    function shouldAutoScrollLog() {
      return logText.scrollHeight - logText.clientHeight - logText.scrollTop <= logAutoScrollThreshold;
    }

    function renderLog(text) {
      const shouldScroll = shouldAutoScrollLog();
      logText.textContent = text || "No log output yet.";
      if (shouldScroll) {
        logText.scrollTop = logText.scrollHeight;
      }
    }

    function playKikkaUnlockEffect() {
      kikkaPanel.classList.remove("unlock-pulse");
      void kikkaPanel.offsetWidth;
      kikkaPanel.classList.add("unlock-pulse");
      setKikkaBtn.classList.remove("unlock-fade-in");
      void setKikkaBtn.offsetWidth;
      setKikkaBtn.classList.add("unlock-fade-in");
    }

    function setBusy(value) {
      busy = value;
      bridgeToggle.disabled = value;
      statusSwitch.classList.toggle("disabled", value);
      clearLogBtn.disabled = value;
    }

    function setStatus(running, pid, source) {
      statusSwitch.classList.toggle("running", running);
      statusSwitch.classList.toggle("stopped", !running);
      statusText.textContent = running ? "Running" : "Stopped";
      pidText.textContent = running && pid ? `PID ${pid} (${source})` : "";
      bridgeToggle.checked = running;
      if (!busy) {
        bridgeToggle.disabled = false;
        statusSwitch.classList.remove("disabled");
        clearLogBtn.disabled = false;
      }
    }

    function currentBridgeRunning() {
      return statusSwitch.classList.contains("running");
    }

    function setWatcherSwitch(switchNode, toggleNode, enabled) {
      switchNode.classList.toggle("running", enabled);
      switchNode.classList.toggle("stopped", !enabled);
      toggleNode.checked = enabled;
      toggleNode.disabled = watcherBusy;
      switchNode.classList.toggle("disabled", watcherBusy);
    }

    function renderWatchers(watchers) {
      const talk = !watchers || watchers.talk !== false;
      const screen = !watchers || watchers.screen !== false;
      setWatcherSwitch(talkWatcherSwitch, talkWatcherToggle, talk);
      setWatcherSwitch(screenWatcherSwitch, screenWatcherToggle, screen);
    }

    function openBridgeSettings() {
      bridgeSettingsOverlay.classList.remove("hidden");
      bridgeSettingsPanel.focus();
    }

    function closeBridgeSettings() {
      bridgeSettingsOverlay.classList.add("hidden");
      bridgeSettingsBtn.focus();
    }

    async function setWatchers(values) {
      watcherBusy = true;
      renderWatchers({ talk: talkWatcherToggle.checked, screen: screenWatcherToggle.checked });
      try {
        const response = await fetch("/api/watchers", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(values)
        });
        const data = await response.json();
        renderWatchers(data);
        message.textContent = data.message || "";
      } catch (error) {
        message.textContent = String(error);
      } finally {
        watcherBusy = false;
        await refresh();
      }
    }

    function setGatewayBusy(value) {
      gatewayBusy = value;
      gatewayToggle.disabled = value;
      gatewayRestartBtn.disabled = value || !currentGatewayRunning();
      gatewayStatusSwitch.classList.toggle("disabled", value);
      reloadGatewayFilesBtn.disabled = value;
      saveGatewayFilesBtn.disabled = value;
    }

    function renderGateway(gateway) {
      const running = Boolean(gateway && gateway.running);
      const displayRunning = gatewayBusy && gatewayPendingRunning !== null ? gatewayPendingRunning : running;
      gatewayStatusSwitch.classList.toggle("running", displayRunning);
      gatewayStatusSwitch.classList.toggle("stopped", !displayRunning);
      gatewayStatusText.textContent = displayRunning ? "Running" : "Stopped";
      gatewayToggle.checked = displayRunning;
      gatewayPidText.textContent = running && gateway.pid ? `PID ${gateway.pid} (${gateway.source})` : "";
      gatewayTitleProfile.textContent = (gateway && gateway.profile) || "--";
      gatewayEditor.disabled = !gatewayFilesHome;
      if (!gatewayBusy) {
        gatewayToggle.disabled = false;
        gatewayRestartBtn.disabled = !running;
        gatewayStatusSwitch.classList.remove("disabled");
        reloadGatewayFilesBtn.disabled = false;
        saveGatewayFilesBtn.disabled = false;
      }
      if (gateway.home && gateway.home !== gatewayFilesHome && !gatewayEditorDirty) {
        loadGatewayFiles(false);
      }
    }

    function currentGatewayRunning() {
      return gatewayStatusSwitch.classList.contains("running");
    }

    function renderGatewayPending(running) {
      gatewayPendingRunning = running;
      gatewayStatusSwitch.classList.toggle("running", running);
      gatewayStatusSwitch.classList.toggle("stopped", !running);
      gatewayStatusText.textContent = running ? "Running" : "Stopped";
      gatewayToggle.checked = running;
    }

    function persistGatewayEditor() {
      if (!gatewayFiles[currentGatewayFile]) {
        gatewayFiles[currentGatewayFile] = { text: "" };
      }
      gatewayFiles[currentGatewayFile].text = gatewayEditor.value;
    }

    function renderGatewayEditor() {
      for (const button of gatewayTabs.querySelectorAll("button")) {
        button.classList.toggle("active", button.dataset.file === currentGatewayFile);
      }
      const file = gatewayFiles[currentGatewayFile];
      gatewayEditor.value = file ? (file.text || "") : "";
      gatewayEditor.placeholder = gatewayFilesHome ? currentGatewayFile : "Gateway profile files unavailable.";
      gatewayEditor.disabled = !gatewayFilesHome;
    }

    async function loadGatewayFiles(force) {
      if (gatewayEditorDirty && !force) {
        return;
      }
      try {
        const response = await fetch("/api/gateway/files", { cache: "no-store" });
        const data = await response.json();
        if (!response.ok || !data.ok) {
          gatewayMessage.textContent = (data && data.message) || "Gateway files unavailable.";
          gatewayFilesHome = "";
          gatewayFiles = {};
          renderGatewayEditor();
          return;
        }
        gatewayFilesHome = (data.gateway && data.gateway.home) || "";
        gatewayFiles = data.files || {};
        gatewayEditorDirty = false;
        gatewayMessage.textContent = gatewayFilesHome ? "Gateway profile files loaded." : "Gateway HERMES_HOME not detected.";
        renderGatewayEditor();
      } catch (error) {
        gatewayMessage.textContent = String(error);
      }
    }

    async function saveGatewayFiles() {
      persistGatewayEditor();
      setGatewayBusy(true);
      try {
        const payload = {
          [currentGatewayFile]: (gatewayFiles[currentGatewayFile] && gatewayFiles[currentGatewayFile].text) || ""
        };
        const response = await fetch("/api/gateway/files", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ files: payload })
        });
        const data = await response.json();
        gatewayMessage.textContent = response.ok && data.ok
          ? `${currentGatewayFile} saved.`
          : (data.message || "");
        if (response.ok && data.ok) {
          gatewayEditorDirty = false;
        }
      } catch (error) {
        gatewayMessage.textContent = String(error);
      } finally {
        setGatewayBusy(false);
        await refresh();
      }
    }

    function sleep(ms) {
      return new Promise((resolve) => window.setTimeout(resolve, ms));
    }

    async function waitForGatewayState(expectedRunning) {
      const deadline = Date.now() + gatewayActionTimeoutMs;
      let latest = null;
      while (Date.now() < deadline) {
        latest = await refresh();
        if (latest && latest.gateway && Boolean(latest.gateway.running) === expectedRunning) {
          return latest;
        }
        await sleep(gatewayActionPollMs);
      }
      return latest || await refresh();
    }

    async function gatewayAction(path) {
      if (gatewayBusy) {
        gatewayToggle.checked = currentGatewayRunning();
        return;
      }
      setGatewayBusy(true);
      gatewayMessage.textContent = "";
      const targetRunning = !path.endsWith("/stop");
      let waitForTarget = false;
      try {
        const response = await fetch(path, { method: "POST" });
        const data = await response.json();
        gatewayMessage.textContent = data.message || "";
        if (response.ok && data.ok !== false) {
          waitForTarget = true;
          renderGatewayPending(targetRunning);
        }
      } catch (error) {
        gatewayMessage.textContent = String(error);
      } finally {
        let latest = null;
        if (waitForTarget) {
          latest = await waitForGatewayState(targetRunning);
        } else {
          latest = await refresh();
        }
        gatewayPendingRunning = null;
        if (latest && latest.gateway) {
          renderGateway(latest.gateway);
        }
        setGatewayBusy(false);
      }
    }

    const sliderStartColor = [255, 0, 0];
    const sliderEndColor = [102, 0, 0];
    const sliderGradient = buildSliderGradient();

    function sliderCurve(t) {
      const clipped = Math.max(0, Math.min(1, Number(t)));
      const p1x = 0.05;
      const p1y = 0.8;
      const p2x = 0.62;
      const p2y = 0.92;
      let low = 0;
      let high = 1;
      let curveT = clipped;
      for (let i = 0; i < 16; i += 1) {
        curveT = (low + high) / 2;
        const x = cubicBezierValue(curveT, p1x, p2x);
        if (x < clipped) {
          low = curveT;
        } else {
          high = curveT;
        }
      }
      return cubicBezierValue(curveT, p1y, p2y);
    }

    function cubicBezierValue(t, p1, p2) {
      const inv = 1 - t;
      return 3 * inv * inv * t * p1 + 3 * inv * t * t * p2 + t * t * t;
    }

    function mixSliderColor(progress) {
      const clipped = Math.max(0, Math.min(1, Number(progress)));
      const mixed = sliderStartColor.map((channel, index) => {
        return Math.round(channel + (sliderEndColor[index] - channel) * clipped);
      });
      return `rgb(${mixed[0]}, ${mixed[1]}, ${mixed[2]})`;
    }

    function buildSliderGradient() {
      const stops = [];
      for (let i = 0; i <= 64; i += 1) {
        const position = i / 64;
        stops.push(`${mixSliderColor(sliderCurve(position))} ${(position * 100).toFixed(2)}%`);
      }
      return `linear-gradient(90deg, ${stops.join(", ")})`;
    }

    function sliderColor(value) {
      return mixSliderColor(sliderCurve(Math.max(0, Math.min(100, Number(value))) / 100));
    }

    function updateSliderVisual(sliderShell, value) {
      sliderShell.style.setProperty("--slider-pos", `${value}%`);
      sliderShell.style.setProperty("--thumb-color", sliderColor(value));
      sliderShell.style.setProperty("--slider-gradient", sliderGradient);
    }

    function currentKikkaValue(values, key) {
      return Object.prototype.hasOwnProperty.call(pendingKikkaValues, key)
        ? pendingKikkaValues[key]
        : (Object.prototype.hasOwnProperty.call(values, key) ? values[key] : null);
    }

    function createKikkaIconRow(key, value) {
      const url = kikkaIconUrls[key] || "";
      const count = Math.max(0, Math.floor(Number(value || 0) / 100));
      const cached = kikkaIconRows[key];
      if (cached && cached.url === url && cached.count === count) {
        return cached.node;
      }
      const row = document.createElement("span");
      row.className = "kikka-icon-row";
      if (!url || count <= 0) {
        kikkaIconRows[key] = { url, count, node: row };
        return row;
      }
      for (let index = 0; index < count; index += 1) {
        const icon = document.createElement("img");
        icon.className = "kikka-stat-icon";
        icon.src = url;
        icon.alt = "";
        icon.decoding = "async";
        icon.loading = "eager";
        row.append(icon);
      }
      kikkaIconRows[key] = { url, count, node: row };
      return row;
    }

    function renderKikka(kikka) {
      const values = (kikka && kikka.values) || {};
      lastKikka = kikka;
      if (!kikkaEditUnlocked) {
        pendingKikkaValues = { ...values };
      } else {
        for (const key of kikkaSetKeys) {
          if (!Object.prototype.hasOwnProperty.call(pendingKikkaValues, key) && Object.prototype.hasOwnProperty.call(values, key)) {
            pendingKikkaValues[key] = values[key];
          }
        }
      }
      kikkaList.classList.toggle("editing", kikkaEditUnlocked);
      kikkaList.innerHTML = "";
      for (const key of kikkaKeys) {
        const term = document.createElement("dt");
        term.textContent = kikkaLabels[key] || key;
        if (kikkaSetKeys.includes(key)) {
          const sliderShell = document.createElement("span");
          sliderShell.className = "slider-shell";
          const slider = document.createElement("input");
          slider.className = "kikka-slider";
          slider.type = "range";
          slider.min = "0";
          slider.max = "100";
          slider.value = Object.prototype.hasOwnProperty.call(pendingKikkaValues, key) ? pendingKikkaValues[key] : (values[key] || 0);
          slider.dataset.key = key;
          slider.disabled = !kikkaEditUnlocked;
          updateSliderVisual(sliderShell, slider.value);
          slider.addEventListener("input", () => {
            pendingKikkaValues[key] = Number(slider.value);
            updateSliderVisual(sliderShell, slider.value);
            const valueNode = kikkaList.querySelector(`[data-value-key="${key}"]`);
            if (valueNode) {
              valueNode.textContent = slider.value;
            }
          });
          sliderShell.append(slider);
          kikkaList.append(term, sliderShell);
        } else if (key === "kikkamood" || key === "intimacy") {
          kikkaList.append(term, createKikkaIconRow(key, currentKikkaValue(values, key)));
        } else {
          const placeholder = document.createElement("span");
          placeholder.className = "slider-placeholder";
          kikkaList.append(term, placeholder);
        }
        const value = document.createElement("dd");
        value.dataset.valueKey = key;
        value.textContent = Object.prototype.hasOwnProperty.call(pendingKikkaValues, key)
          ? pendingKikkaValues[key]
          : (Object.prototype.hasOwnProperty.call(values, key) ? values[key] : "--");
        kikkaList.append(value);
      }
      if (kikka) {
        const stamp = kikka.updated_at ? ` (${kikka.updated_at})` : "";
        if (Date.now() >= kikkaNoticeUntil) {
          kikkaMessage.textContent = `${kikka.message || ""}${stamp}`;
        }
      }
    }

    function setKikkaNotice(text, durationMs = 5000) {
      kikkaNoticeUntil = Date.now() + durationMs;
      kikkaMessage.textContent = text;
    }

    function unlockKikkaEdit() {
      if (kikkaEditUnlocked) {
        return;
      }
      kikkaEditUnlocked = true;
      setKikkaBtn.classList.remove("hidden");
      playKikkaUnlockEffect();
      pendingKikkaValues = { ...((lastKikka && lastKikka.values) || {}) };
      renderKikka(lastKikka || { values: pendingKikkaValues, message: "Edit mode unlocked.", updated_at: "" });
      setKikkaNotice("Edit mode unlocked.");
    }

    async function refresh() {
      try {
        const response = await fetch("/api/status", { cache: "no-store" });
        const data = await response.json();
        setStatus(data.bridge.running, data.bridge.pid, data.bridge.source);
        applyTheme(data.theme);
        renderLog(data.log);
        renderKikka(data.kikka);
        renderGateway(data.gateway);
        renderWatchers(data.watchers);
        if (!data.bridge.running && data.bridge.last_exit && !data.bridge.last_exit.expected && !busy) {
          const exit = data.bridge.last_exit;
          message.textContent = `Bridge exited at ${exit.time} with code ${exit.returncode}. See ${exit.stderr_log}.`;
        }
        return data;
      } catch (error) {
        statusSwitch.classList.remove("running");
        statusSwitch.classList.add("stopped");
        statusText.textContent = "Unavailable";
        message.textContent = String(error);
        return null;
      }
    }

    async function action(path, options = {}) {
      if (busy) {
        bridgeToggle.checked = currentBridgeRunning();
        return;
      }
      setBusy(true);
      message.textContent = "";
      try {
        const response = await fetch(path, { method: "POST" });
        const data = await response.json();
        message.textContent = data.message || "";
      } catch (error) {
        message.textContent = String(error);
      } finally {
        await refresh();
        const cooldownMs = Math.max(0, Number(options.cooldownMs || 0));
        if (cooldownMs > 0) {
          window.setTimeout(() => setBusy(false), cooldownMs);
        } else {
          setBusy(false);
        }
      }
    }

    async function refreshKikka() {
      refreshKikkaBtn.disabled = true;
      kikkaNoticeUntil = 0;
      kikkaMessage.textContent = "";
      try {
        const response = await fetch("/api/kikka/refresh", { method: "POST" });
        const data = await response.json();
        pendingKikkaValues = { ...((data.kikka && data.kikka.values) || {}) };
        renderKikka(data.kikka);
      } catch (error) {
        kikkaMessage.textContent = String(error);
      } finally {
        refreshKikkaBtn.disabled = false;
      }
    }

    async function setKikka() {
      setKikkaBtn.disabled = true;
      kikkaMessage.textContent = "";
      const values = {};
      for (const key of kikkaSetKeys) {
        values[key] = Number(pendingKikkaValues[key] || 0);
      }
      try {
        const response = await fetch("/api/kikka/set", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ values })
        });
        const data = await response.json();
        pendingKikkaValues = { ...((data.kikka && data.kikka.values) || values) };
        renderKikka(data.kikka);
        setKikkaNotice((data.kikka && data.kikka.message) || "Kikka variables set.");
      } catch (error) {
        setKikkaNotice(String(error));
      } finally {
        setKikkaBtn.disabled = false;
      }
    }

    async function setTheme(dark) {
      applyTheme({ dark });
      try {
        const response = await fetch("/api/theme", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dark })
        });
        const data = await response.json();
        applyTheme(data.theme);
      } catch (error) {
        message.textContent = String(error);
      }
    }

    bridgeToggle.addEventListener("change", () => {
      action(bridgeToggle.checked ? "/api/bridge/start" : "/api/bridge/stop", { cooldownMs: bridgeActionCooldownMs });
    });
    clearLogBtn.addEventListener("click", () => action("/api/log/clear"));
    bridgeSettingsBtn.addEventListener("click", openBridgeSettings);
    closeBridgeSettingsBtn.addEventListener("click", closeBridgeSettings);
    bridgeSettingsOverlay.addEventListener("click", (event) => {
      if (event.target === bridgeSettingsOverlay) {
        closeBridgeSettings();
      }
    });
    talkWatcherToggle.addEventListener("change", () => {
      setWatchers({ talk: talkWatcherToggle.checked });
    });
    screenWatcherToggle.addEventListener("change", () => {
      setWatchers({ screen: screenWatcherToggle.checked });
    });
    themeToggle.addEventListener("change", () => setTheme(themeToggle.checked));
    setKikkaBtn.addEventListener("click", setKikka);
    refreshKikkaBtn.addEventListener("click", refreshKikka);
    gatewayToggle.addEventListener("change", () => {
      gatewayAction(gatewayToggle.checked ? "/api/gateway/start" : "/api/gateway/stop");
    });
    gatewayRestartBtn.addEventListener("click", () => gatewayAction("/api/gateway/restart"));
    gatewayTabs.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-file]");
      if (!button) {
        return;
      }
      persistGatewayEditor();
      currentGatewayFile = button.dataset.file;
      renderGatewayEditor();
    });
    gatewayEditor.addEventListener("input", () => {
      gatewayEditorDirty = true;
      persistGatewayEditor();
    });
    reloadGatewayFilesBtn.addEventListener("click", () => loadGatewayFiles(true));
    saveGatewayFilesBtn.addEventListener("click", saveGatewayFiles);
    window.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !bridgeSettingsOverlay.classList.contains("hidden")) {
        closeBridgeSettings();
        return;
      }
      const expected = konamiCode[konamiIndex];
      if (event.code === expected) {
        konamiIndex += 1;
        if (konamiIndex === konamiCode.length) {
          konamiIndex = 0;
          unlockKikkaEdit();
        }
      } else {
        konamiIndex = event.code === konamiCode[0] ? 1 : 0;
      }
    });
    Promise.allSettled([refresh(), preloadKikkaIcons()]).finally(finishLoading);
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesBridgeControl/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        _service_log(fmt % args)

    def _send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'; "
            "connect-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:",
        )

    @staticmethod
    def _header_hostname(value: str) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        parsed = urlparse(value if "://" in value else f"//{value}")
        return (parsed.hostname or "").lower()

    def _allow_request(self, *, mutating: bool = False) -> bool:
        host = self._header_hostname(self.headers.get("Host", ""))
        if host not in LOCAL_HOSTS:
            self._send_json({"ok": False, "message": "Local Host header required."}, HTTPStatus.FORBIDDEN)
            return False
        if mutating:
            for header in ("Origin", "Referer"):
                value = self.headers.get(header, "").strip()
                if value and self._header_hostname(value) not in LOCAL_HOSTS:
                    self._send_json({"ok": False, "message": "Cross-origin request rejected."}, HTTPStatus.FORBIDDEN)
                    return False
        return True

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self) -> None:
        theme = "dark" if _theme_status().get("dark") else "light"
        html = INDEX_HTML.replace("__INITIAL_THEME__", theme)
        data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        if length > MAX_REQUEST_BODY:
            raise RequestBodyTooLarge("Request body exceeds 4 MiB limit.")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("Content-Type must be application/json.")
        raw = self.rfile.read(length).decode("utf-8", errors="strict")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object.")
        return data

    def do_GET(self) -> None:
        if not self._allow_request():
            return
        path = urlparse(self.path).path
        if path == "/":
            self._send_html()
        elif path == "/api/status":
            self._send_json(_api_status())
        elif path == "/api/gateway/files":
            payload = _read_gateway_files()
            self._send_json(payload, HTTPStatus.OK if payload["ok"] else HTTPStatus.BAD_REQUEST)
        else:
            self._send_json({"ok": False, "message": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._allow_request(mutating=True):
            return
        path = urlparse(self.path).path
        try:
            self._dispatch_post(path)
        except RequestBodyTooLarge as exc:
            self._send_json({"ok": False, "message": str(exc)}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._send_json({"ok": False, "message": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _dispatch_post(self, path: str) -> None:
        if path == "/api/bridge/start":
            ok, message = _start_bridge()
            self._send_json({"ok": ok, "message": message}, HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST)
        elif path == "/api/bridge/stop":
            ok, message = _stop_bridge()
            self._send_json({"ok": ok, "message": message}, HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST)
        elif path == "/api/log/clear":
            ok, message = _clear_log()
            self._send_json({"ok": ok, "message": message}, HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST)
        elif path == "/api/kikka/refresh":
            state = _refresh_kikka_vars()
            self._send_json({"ok": bool(state["ok"]), "kikka": state}, HTTPStatus.OK if state["ok"] else HTTPStatus.BAD_REQUEST)
        elif path == "/api/kikka/set":
            data = self._read_json()
            state = _set_kikka_vars(data.get("values", {}))
            if state["ok"]:
                self._send_json({"ok": True, "kikka": state})
            else:
                self._send_json({"ok": False, "kikka": state}, HTTPStatus.BAD_REQUEST)
        elif path == "/api/theme":
            data = self._read_json()
            theme = _set_theme(bool(data.get("dark")))
            self._send_json({"ok": True, "theme": theme})
        elif path == "/api/watchers":
            data = self._read_json()
            state = _set_watcher_control(data)
            self._send_json(state, HTTPStatus.OK if state["ok"] else HTTPStatus.BAD_REQUEST)
        elif path == "/api/gateway/start":
            ok, message = _start_gateway()
            self._send_json({"ok": ok, "message": message}, HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST)
        elif path == "/api/gateway/stop":
            ok, message = _stop_gateway()
            self._send_json({"ok": ok, "message": message}, HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST)
        elif path == "/api/gateway/restart":
            ok, message = _restart_gateway()
            self._send_json({"ok": ok, "message": message}, HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST)
        elif path == "/api/gateway/files":
            data = self._read_json()
            payload = _save_gateway_files(data.get("files", {}))
            self._send_json(payload, HTTPStatus.OK if payload["ok"] else HTTPStatus.BAD_REQUEST)
        else:
            self._send_json({"ok": False, "message": "Not found"}, HTTPStatus.NOT_FOUND)


def serve() -> None:
    cfg = config()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    _init_theme_from_system()
    _refresh_kikka_vars()
    server = LocalControlServer(("127.0.0.1", int(cfg["port"])), Handler)
    CONTROL_PID_FILE.write_text(str(os.getpid()), encoding="ascii")
    _service_log(f"control service listening on 127.0.0.1:{cfg['port']}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        try:
            if CONTROL_PID_FILE.read_text(encoding="ascii").strip() == str(os.getpid()):
                CONTROL_PID_FILE.unlink()
        except (FileNotFoundError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hermes SSP Bridge control service")
    parser.add_argument("--check", action="store_true", help="validate config and exit")
    args = parser.parse_args(argv)
    if args.check:
        print(json.dumps(build_check_report(), ensure_ascii=False, indent=2))
        return 0
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
