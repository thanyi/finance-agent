"""Exercise Windows transport/encoding/capture/mutexes without broker actions."""
from __future__ import annotations

import json
import struct
import subprocess
import sys
import uuid

from windows_core import BrokerError
from windows_bridge import Bridge, DATA_ROOT, MUTEX_NAME, SKILL_ROOT, _kernel, desktop_lock, named_mutex, save_json


def _child(name: str, hold=False):
    code = ("import sys,os,json;sys.path.insert(0," + repr(str(SKILL_ROOT / "scripts")) + ");"
            "from windows_bridge import named_mutex;from windows_core import BrokerError\n"
            "try:\n"
            " with named_mutex(" + repr(name) + "):\n"
            "  print('READY',flush=True)\n" +
            ("  sys.stdin.read(1);os._exit(0)\n" if hold else "") +
            "except BrokerError as e:\n print(e.code,flush=True);sys.exit(2)\n")
    return [sys.executable, "-I", "-X", "utf8", "-c", code]


def run(output, directory):
    checks = []
    with desktop_lock(DATA_ROOT):
        bridge = Bridge(output)
        echo = "证券代码 / 中文路径 / 001234"
        probe = bridge.probe(directory, echo)
        if probe.get("echo") != echo or probe.get("transport") != "windows_local" or probe.get("desktop") != "Default":
            raise BrokerError("native_selftest_failed", "Native PowerShell or UTF-8 round trip failed")
        checks.append("interactive_windows_and_utf8_round_trip")
        digest = bridge.capture(directory / "native-screen.png")
        header = (directory / "native-screen.png").read_bytes()
        width, height = struct.unpack(">II", header[16:24])
        if width <= 0 or height <= 0:
            raise BrokerError("native_selftest_failed", "Native screenshot has invalid dimensions")
        checks.append("native_png_capture")
        blocked = subprocess.run(_child(MUTEX_NAME), capture_output=True, encoding="utf-8", timeout=15)
        if blocked.returncode != 2 or blocked.stdout.strip() != "desktop_busy":
            raise BrokerError("native_selftest_failed", "A second process did not respect the desktop mutex")
        checks.append("cross_process_and_cross_installation_exclusion")
    released = subprocess.run(_child(MUTEX_NAME), capture_output=True, encoding="utf-8", timeout=15)
    if released.returncode != 0 or released.stdout.strip() != "READY":
        raise BrokerError("native_selftest_failed", "Desktop mutex remained locked after release")
    checks.append("mutex_release")

    # Keep a second handle alive so the kernel preserves abandoned state.
    unique = MUTEX_NAME + ".selftest." + uuid.uuid4().hex
    owner = subprocess.Popen(_child(unique, hold=True), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, encoding="utf-8")
    api = _kernel()
    observer = None
    try:
        # communicate is deadline-bounded; ready is an immediate local write.
        import threading
        ready = []
        reader = threading.Thread(target=lambda: ready.append(owner.stdout.readline()), daemon=True)
        reader.start()
        reader.join(10)
        if not ready or ready[0].strip() != "READY":
            raise BrokerError("native_selftest_failed", "Cannot start the isolated mutex-abandonment worker")
        observer = api.CreateMutexW(None, False, unique)
        if not observer:
            raise BrokerError("native_selftest_failed", "Cannot observe the test mutex")
        owner.communicate("x", timeout=10)
        try:
            with named_mutex(unique):
                raise BrokerError("native_selftest_failed", "An abandoned mutex was accepted without review")
        except BrokerError as exc:
            if exc.code != "desktop_recovery_required":
                raise
        with named_mutex(unique):
            pass
        checks.append("abandoned_worker_requires_review_then_releases")
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.communicate(timeout=10)
        if observer:
            api.CloseHandle(observer)
    result = {"status": "passed", "scenario": "native", "evidence_kind": "windows_native_runtime",
              "checks": checks, "environment": probe, "storage": bridge.storage, "python": sys.version.split()[0],
              "screen": {"width": width, "height": height, "sha256": digest},
              "broker_action_performed": False, "evidence": str(directory)}
    save_json(directory / "test.json", result)
    return result
