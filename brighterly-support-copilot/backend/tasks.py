"""Cross-platform task runner: python tasks.py dev|reset|demo|zip"""
import subprocess, sys, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dev"
    port = os.environ.get("PORT", "8000")
    if cmd == "dev":
        return subprocess.call([sys.executable, "-m", "uvicorn", "app.main:app", "--reload", "--host", "0.0.0.0", "--port", port])
    if cmd == "reset":
        return subprocess.call([sys.executable, "scripts/reset_demo.py"])
    if cmd == "demo":
        return subprocess.call([sys.executable, "scripts/demo_run.py", *sys.argv[2:]])
    if cmd == "zip":
        return subprocess.call([sys.executable, "scripts/make_zip.py"])
    print("usage: python tasks.py [dev|reset|demo|zip]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
