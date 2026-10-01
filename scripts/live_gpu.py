"""Optional read-only collection from an existing local nvidia-smi installation.

No installation, tuning, resets, remote calls, or telemetry upload occurs.
"""

import csv
from datetime import datetime, timezone
import json
import shutil
import subprocess

FIELDS = [
    "uuid",
    "name",
    "memory.total",
    "memory.used",
    "utilization.gpu",
    "temperature.gpu",
    "power.draw",
]


def collect():
    executable = shutil.which("nvidia-smi")
    if not executable:
        raise RuntimeError(
            "nvidia-smi is unavailable. Use the hardware-free replay, or run this on a supported NVIDIA system with its driver installed."
        )
    result = subprocess.run(
        [executable, "--query-gpu=" + ",".join(FIELDS), "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    rows = [
        dict(zip(FIELDS, [value.strip() for value in row]))
        for row in csv.reader(result.stdout.splitlines())
    ]
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "real local nvidia-smi read-only query",
        "rows": rows,
        "units": {
            "memory.total": "MiB",
            "memory.used": "MiB",
            "utilization.gpu": "%",
            "temperature.gpu": "C",
            "power.draw": "W",
        },
    }


if __name__ == "__main__":
    try:
        print(json.dumps(collect(), indent=2))
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
