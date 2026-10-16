"""Build a standalone, credential-free Windows skill ZIP with file hashes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

SKILL_ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("SKILL.md", "install.ps1", "agents/openai.yaml", "scripts/eastmoney.py",
            "scripts/windows_core.py", "scripts/windows_bridge.py", "scripts/windows_host.ps1",
            "scripts/windows_selftest.py", "scripts/desktop.ps1", "references/windows.md",
            "references/recovery.md", "references/simulation-web.md", "references/validation.md")


def build(destination: Path, source: Path = SKILL_ROOT) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if destination.is_relative_to(source):
        raise ValueError("Archive must be outside the skill source directory")
    for name in REQUIRED:
        if not (source / name).is_file():
            raise ValueError("Missing required skill file: " + name)
    for document in source.rglob("*.md"):
        for link in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if "://" not in link and not link.startswith("#") and not (document.parent / link.split("#")[0]).is_file():
                raise ValueError("Missing linked resource: " + link)
    payload = {}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(p in {"__pycache__", "data", ".git"} for p in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError("Standalone Windows skills cannot contain symbolic links")
        if path.is_file() and path.suffix in {".md", ".yaml", ".py", ".ps1"}:
            payload[relative.as_posix()] = path.read_bytes()
    manifest = {"skill": "eastmoney-windows", "files": {
        name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        for name, raw in payload.items()}}
    payload["MANIFEST.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for name, raw in payload.items():
            info = ZipInfo("eastmoney-windows/" + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix + ".sha256").write_text(digest + "  " + destination.name + "\n", encoding="utf-8")
    return {"status": "built", "file": str(destination), "sha256": digest, "files": len(payload)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output), ensure_ascii=False))
