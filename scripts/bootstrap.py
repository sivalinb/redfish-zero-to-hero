"""Create an isolated environment, install the pinned dependencies, and start UI."""

from pathlib import Path
import subprocess
import sys
import json

ROOT = Path(__file__).resolve().parents[1]
if not (3, 11) <= sys.version_info[:2] <= (3, 13):
    raise SystemExit("Use Python 3.11, 3.12, or 3.13.")
env = ROOT / ".venv"
python = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
if not python.exists():
    subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
subprocess.run(
    [str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=True
)
port = json.loads((ROOT / "content/course.json").read_text())["port"]
subprocess.run(
    [
        str(python),
        "-m",
        "streamlit",
        "run",
        str(ROOT / "app.py"),
        "--server.address=127.0.0.1",
        f"--server.port={port}",
    ],
    cwd=ROOT,
    check=True,
)
