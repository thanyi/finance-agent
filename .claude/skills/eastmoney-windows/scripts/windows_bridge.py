"""Windows-local PowerShell transport, private artifacts and a desktop mutex."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

from windows_core import BrokerError, now

SKILL_ROOT = Path(__file__).resolve().parents[1]
HOST_SCRIPT = SKILL_ROOT / "scripts/windows_host.ps1"
DESKTOP_SCRIPT = SKILL_ROOT / "scripts/desktop.ps1"
_project = next((p for p in SKILL_ROOT.parents if (p / ".git").exists()), SKILL_ROOT)
DATA_ROOT = (Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) /
             "FinanceAgent/eastmoney-windows" if os.name == "nt" else _project / "data/eastmoney-windows")
MUTEX_NAME = r"Local\FinanceAgent.EastmoneyDesktop"


def require_windows():
    if os.name != "nt":
        raise BrokerError("windows_required", "Run this command in an interactive Windows desktop, using Windows Python")


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def new_run(output: Path, command: str) -> Path:
    name = now().replace("-", "").replace(":", "").split("+")[0].replace("T", "-")
    directory = output / "runs" / (name + "-" + uuid.uuid4().hex[:10] + "-" + command)
    directory.mkdir(parents=True, mode=0o700)
    return directory


def _kernel():
    require_windows()
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    api.CreateMutexW.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.ReleaseMutex.argtypes = [wintypes.HANDLE]
    api.ReleaseMutex.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


@contextmanager
def named_mutex(name: str):
    api = _kernel()
    handle = api.CreateMutexW(None, False, name)
    if not handle:
        raise BrokerError("lock_failed", "Cannot create the Windows desktop mutex")
    owned = False
    try:
        outcome = api.WaitForSingleObject(handle, 0)
        if outcome == 0x102:
            raise BrokerError("desktop_busy", "Another process owns the Eastmoney desktop")
        if outcome not in (0, 0x80):
            raise BrokerError("lock_failed", "Cannot acquire the Windows desktop mutex")
        owned = True
        if outcome == 0x80:
            raise BrokerError("desktop_recovery_required", "Previous desktop worker ended unexpectedly; inspect the UI with doctor before retrying")
        yield
    finally:
        if owned:
            api.ReleaseMutex(handle)
        api.CloseHandle(handle)


@contextmanager
def desktop_lock(output: Path):
    # A session-wide name also serializes different installations/output folders.
    with named_mutex(MUTEX_NAME):
        yield


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class Bridge:
    def __init__(self, output: Path, client_pid: int | None = None):
        require_windows()
        self.output = output.resolve()
        self.client_pid = client_pid
        self.output.relative_to(DATA_ROOT.resolve())
        if str(self.output).startswith("\\\\") or str(SKILL_ROOT).startswith("\\\\"):
            raise BrokerError("local_path_required", "Copy the skill and runtime files to a local Windows disk")
        self.output.mkdir(parents=True, exist_ok=True)
        self.powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        self.config_path = self.output / "config.json"
        # Protect the whole dedicated runtime tree before reading account state.
        secure = self._invoke({"host_action": "secure", "path": str(DATA_ROOT.resolve())}, self.output)
        self._require_host_ok(secure)
        self.storage = secure
        self.config = json.loads(self.config_path.read_text(encoding="utf-8")) if self.config_path.exists() else {}

    @staticmethod
    def _require_host_ok(result):
        if result.get("status") == "error":
            raise BrokerError(result.get("code", "windows_host_error"), result.get("message", "Windows host failed"))
        return result

    def _invoke(self, request: dict, run_dir: Path) -> dict:
        request_file = run_dir / ("request-" + uuid.uuid4().hex + ".json")
        save_json(request_file, request)
        bootstrap = ("$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
                     "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
                     "& ([scriptblock]::Create([IO.File]::ReadAllText(" + ps_quote(str(HOST_SCRIPT)) +
                     ",[Text.Encoding]::UTF8))) -RequestPath " + ps_quote(str(request_file)))
        encoded = base64.b64encode(bootstrap.encode("utf-16le")).decode("ascii")
        try:
            process = subprocess.run([str(self.powershell), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                                      "-EncodedCommand", encoded], capture_output=True, timeout=60,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                raw = process.stdout.decode("utf-8-sig").strip()
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError("Expected JSON object")
            except (ValueError, UnicodeError):
                raise BrokerError("invalid_driver_response", "Windows PowerShell did not return a JSON object") from None
            if process.returncode and result.get("status") != "error":
                raise BrokerError("windows_host_error", "PowerShell failed despite returning an apparent result")
            return result
        except FileNotFoundError:
            raise BrokerError("powershell_missing", "Windows PowerShell 5.1 was not found") from None
        except subprocess.TimeoutExpired:
            raise BrokerError("desktop_timeout", "Desktop operation exceeded 60 seconds; inspect the UI before retrying") from None
        finally:
            request_file.unlink(missing_ok=True)

    def local_path(self, path: Path) -> str:
        path = path.resolve()
        path.relative_to(self.output)
        return str(path)

    def run(self, action: str, run_dir: Path, **parameters) -> dict:
        run_dir.resolve().relative_to(self.output)
        request = {"host_action": "driver", "driver_path": str(DESKTOP_SCRIPT), "driver_request": {
            "action": action, "profile": self.config.get("profile"), "client_pid": self.client_pid,
            "auth_required": self.config.get("auth_required", False), **parameters}}
        return self._invoke(request, run_dir)

    def capture(self, path: Path) -> str:
        self.local_path(path)
        self._require_host_ok(self._invoke({"host_action": "capture", "path": str(path.resolve())}, path.parent))
        raw = path.read_bytes()
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise BrokerError("capture_failed", "Windows screenshot is not a valid PNG")
        return hashlib.sha256(raw).hexdigest()

    def probe(self, directory: Path, echo: str):
        return self._require_host_ok(self._invoke({"host_action": "probe", "echo": echo}, directory))

    def save_config(self):
        # Paths/PIDs from previous sessions are not transport configuration.
        save_json(self.config_path, self.config)
