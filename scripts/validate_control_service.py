#!/usr/bin/env python3
"""Smoke validation for the local bridge control service."""

from __future__ import annotations

import importlib.util
import http.client
import json
import tempfile
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "bridge_control" / "control_service.py"


def request(port: int, method: str, path: str, headers: dict[str, str], body: bytes = b""):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.putrequest(method, path, skip_host=True)
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        response.read()
        return response.status, dict(response.getheaders())
    finally:
        connection.close()


def validate_http_boundary(module) -> None:
    module._service_log = lambda _message: None
    server = module.LocalControlServer(("127.0.0.1", 0), module.Handler)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers = request(port, "GET", "/", {"Host": f"127.0.0.1:{port}"})
        if status != 200 or headers.get("X-Frame-Options") != "DENY":
            raise RuntimeError("Local control response or security headers are invalid")

        status, _ = request(port, "GET", "/", {"Host": "evil.example"})
        if status != 403:
            raise RuntimeError("Foreign Host header was not rejected")

        status, _ = request(
            port,
            "POST",
            "/api/theme",
            {
                "Host": f"127.0.0.1:{port}",
                "Origin": "https://evil.example",
                "Content-Type": "application/json",
                "Content-Length": "2",
            },
            b"{}",
        )
        if status != 403:
            raise RuntimeError("Foreign Origin header was not rejected")

        status, _ = request(
            port,
            "POST",
            "/api/theme",
            {
                "Host": f"127.0.0.1:{port}",
                "Content-Type": "application/json",
                "Content-Length": str(module.MAX_REQUEST_BODY + 1),
            },
        )
        if status != 413:
            raise RuntimeError("Oversized request did not return HTTP 413")

        try:
            duplicate = module.LocalControlServer(("127.0.0.1", port), module.Handler)
        except OSError:
            duplicate = None
        else:
            duplicate.server_close()
            raise RuntimeError("Control port was unexpectedly shared by a second server")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def validate_gateway_lifecycle(module) -> None:
    original_status = module._gateway_status
    original_run = module._run_gateway_cli
    original_wait = module._wait_for_gateway_state
    actions: list[str] = []
    try:
        module._run_gateway_cli = lambda action, timeout=90.0: (actions.append(action) or True, f"{action} ok")
        module._wait_for_gateway_state = lambda expected, timeout=30.0: {"running": expected}

        module._gateway_status = lambda: {"running": False, "pid": None}
        if module._start_gateway() != (True, "Gateway started."):
            raise RuntimeError("Gateway start did not use the official lifecycle command")

        module._gateway_status = lambda: {"running": True, "pid": 1234}
        if module._stop_gateway() != (True, "Gateway stopped."):
            raise RuntimeError("Gateway stop did not use the official lifecycle command")
        if module._restart_gateway() != (True, "Gateway restarted."):
            raise RuntimeError("Gateway restart did not use the official lifecycle command")
        if actions != ["start", "stop", "restart"]:
            raise RuntimeError(f"Unexpected gateway lifecycle calls: {actions}")
        cleaned = module._without_gateway_pid(
            "Gateway started via direct spawn (PID 1234) (PID: 1234)"
        )
        if "1234" in cleaned or "PID" in cleaned:
            raise RuntimeError("Gateway PID was not removed from the user-facing message")
    finally:
        module._gateway_status = original_status
        module._run_gateway_cli = original_run
        module._wait_for_gateway_state = original_wait


def validate_gateway_file_backup(module) -> None:
    original_status = module._gateway_status
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.yaml"
            config_path.write_text("model: old\n", encoding="utf-8")
            module._gateway_status = lambda: {"running": False, "home": str(root)}
            result = module._save_gateway_files({"config.yaml": "model: new\n"})
            backups = list(root.glob("config.yaml.web-backup.*"))
            if not result["ok"] or config_path.read_text(encoding="utf-8") != "model: new\n":
                raise RuntimeError("config.yaml was not saved")
            if len(backups) != 1 or backups[0].read_text(encoding="utf-8") != "model: old\n":
                raise RuntimeError("config.yaml backup was not created correctly")
            if "config.yaml" not in module.GATEWAY_PROFILE_FILES:
                raise RuntimeError("config.yaml is missing from the profile editor")
    finally:
        module._gateway_status = original_status


def main() -> int:
    spec = importlib.util.spec_from_file_location("bridge_control_service", SERVICE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {SERVICE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.build_check_report()
    if report["host"] != "127.0.0.1":
        raise RuntimeError("Control service must bind to localhost only")
    if int(report["port"]) != 1313:
        raise RuntimeError("Default control service port must be 1313")
    validate_http_boundary(module)
    report["http_boundary"] = "ok"
    validate_gateway_lifecycle(module)
    report["gateway_lifecycle"] = "ok"
    validate_gateway_file_backup(module)
    report["gateway_file_backup"] = "ok"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
