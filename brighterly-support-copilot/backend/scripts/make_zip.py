"""Builds dist/brighterly-support-copilot.zip without secrets, caches, runtime state or bytecode."""
import sys, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "dist" / "brighterly-support-copilot.zip"
EXCLUDE_DIRS = {"__pycache__", ".venv", "venv", "cache", "dist", ".git", ".pytest_cache", "node_modules"}
EXCLUDE_NAMES = {".env", "runtime.json", "seed.json", ".DS_Store"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".tmp", ".log"}

OUT.parent.mkdir(exist_ok=True)
n = 0
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if p.is_dir() or set(rel.parts) & EXCLUDE_DIRS or p.name in EXCLUDE_NAMES or p.suffix in EXCLUDE_SUFFIX:
            continue
        z.write(p, Path("brighterly-support-copilot") / rel)
        n += 1
print(f"Wrote {OUT} ({n} files, {OUT.stat().st_size // 1024} KB)")
