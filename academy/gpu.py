"""Transparent arithmetic and telemetry replay, never claimed as GPU execution."""

import math
import re

GIB = 1024**3
DTYPES = {"FP32": 4, "FP16": 2, "INT8": 1}


def memory_plan(
    parameters_billion=7,
    dtype="FP16",
    vram_gib=24,
    batch=1,
    kv_gib_per_request=1,
    overhead_gib=2,
    training=False,
    optimizer_bytes_per_parameter=12,
):
    vals = (
        parameters_billion,
        vram_gib,
        batch,
        kv_gib_per_request,
        overhead_gib,
        optimizer_bytes_per_parameter,
    )
    if not all(isinstance(x, (float, int)) and math.isfinite(x) for x in vals):
        raise ValueError("Use finite numeric inputs.")
    if dtype not in DTYPES or not 0 < parameters_billion <= 1000 or not 1 <= vram_gib <= 1024:
        raise ValueError("Use a known dtype, 0–1000 billion parameters, and 1–1024 GiB VRAM.")
    if (
        not isinstance(batch, int)
        or not 1 <= batch <= 256
        or not 0 <= kv_gib_per_request <= 32
        or not 0 <= overhead_gib <= 256
    ):
        raise ValueError("Batch 1–256; per-request memory 0–32 GiB; overhead 0–256 GiB.")
    if not isinstance(training, bool) or not 0 <= optimizer_bytes_per_parameter <= 32:
        raise ValueError("Invalid training memory configuration.")
    weights = parameters_billion * 1e9 * DTYPES[dtype] / GIB
    optimizer = parameters_billion * 1e9 * optimizer_bytes_per_parameter / GIB if training else 0
    request_memory = batch * kv_gib_per_request
    total = weights + optimizer + request_memory + overhead_gib
    return {
        "weights_gib": weights,
        "optimizer_gib": optimizer,
        "requests_gib": request_memory,
        "overhead_gib": overhead_gib,
        "total_gib": total,
        "vram_gib": vram_gib,
        "free_gib": vram_gib - total,
        "fits": total <= vram_gib,
        "formula": "parameters × bytes/parameter + optimizer allowance + batch × per-request allowance + overhead",
        "mode": "Arithmetic estimate; actual activations, KV cache, fragmentation, kernels and framework allocations vary.",
    }


def replay(scenario="healthy"):
    if scenario not in ("healthy", "memory_pressure", "thermal", "input_starved", "gpu_error"):
        raise ValueError("Unknown telemetry replay")
    rows = []
    for second in range(0, 61, 10):
        values = {
            "second": second,
            "gpu": "0",
            "utilization_percent": 78,
            "temperature_celsius": 55,
            "power_watts": 180,
            "memory_used_mib": 12000,
            "memory_total_mib": 24576,
            "sm_clock_mhz": 1500,
            "xid_code": 0,
            "requests_total": 100 + second * 4,
        }
        if scenario == "memory_pressure":
            values.update(memory_used_mib=min(24576, 17000 + second * 125), utilization_percent=90)
        if scenario == "thermal":
            values.update(
                temperature_celsius=55 + second * 0.6,
                sm_clock_mhz=1500 if second < 40 else 900,
                power_watts=250,
                requests_total=100 + min(second, 40) * 4 + max(second - 40, 0) * 2,
            )
        if scenario == "input_starved":
            values.update(utilization_percent=15, power_watts=65, requests_total=100 + second)
        if scenario == "gpu_error":
            values.update(
                xid_code=48 if second >= 40 else 0,
                utilization_percent=0 if second >= 40 else 78,
                requests_total=100 + min(second, 40) * 4,
            )
        rows.append(values)
    return rows


def exposition(rows):
    last = rows[-1]
    fields = {
        "DCGM_FI_DEV_GPU_TEMP": ("gauge", "temperature_celsius"),
        "DCGM_FI_DEV_POWER_USAGE": ("gauge", "power_watts"),
        "DCGM_FI_DEV_FB_USED": ("gauge", "memory_used_mib"),
        "DCGM_FI_DEV_FB_TOTAL": ("gauge", "memory_total_mib"),
        "DCGM_FI_DEV_GPU_UTIL": ("gauge", "utilization_percent"),
        "DCGM_FI_DEV_SM_CLOCK": ("gauge", "sm_clock_mhz"),
        "DCGM_FI_DEV_XID_ERRORS": ("gauge", "xid_code"),
        "training_requests_total": ("counter", "requests_total"),
    }
    return "# Synthetic training fixture; not read from a GPU\n" + "".join(
        f'# TYPE {name} {kind}\n{name}{{gpu="0",modelName="Training GPU"}} {last[key]}\n'
        for name, (kind, key) in fields.items()
    )


def diagnose(rows):
    last = rows[-1]
    if last["xid_code"]:
        return "gpu_error"
    if last["temperature_celsius"] >= 85 and last["sm_clock_mhz"] < rows[0]["sm_clock_mhz"]:
        return "thermal"
    if last["memory_used_mib"] / last["memory_total_mib"] > 0.90:
        return "memory_pressure"
    if last["utilization_percent"] < 25 and last["power_watts"] < 100:
        return "input_starved"
    return "healthy"


def scheduling(nodes, requests):
    """Whole-GPU first-fit exercise; no hardware, Kubernetes, MIG or fractional allocation."""
    if len(nodes) > 16 or len(requests) > 64:
        raise ValueError("Training scheduler supports at most 16 nodes and 64 jobs.")
    remaining = {name: count for name, count in nodes.items()}
    if not all(isinstance(x, int) and 0 <= x <= 16 for x in remaining.values()):
        raise ValueError("Node capacity must be a whole number of GPUs.")
    result = []
    for number, count in enumerate(requests):
        if not isinstance(count, int) or not 1 <= count <= 16:
            raise ValueError("Jobs request 1–16 whole GPUs.")
        node = next((n for n, available in remaining.items() if available >= count), None)
        if node:
            remaining[node] -= count
        result.append(
            {
                "job": number + 1,
                "gpus": count,
                "node": node,
                "state": "Assigned" if node else "Pending",
            }
        )
    return {"allocations": result, "remaining": remaining}


def validate_manifest(doc):
    if not isinstance(doc, dict):
        raise ValueError("A manifest is a YAML object.")
    containers = doc.get("spec", {}).get("containers", [])
    if doc.get("kind") != "Pod" or doc.get("apiVersion") != "v1" or not containers:
        raise ValueError("Use apiVersion: v1, kind: Pod, and at least one container.")
    total = 0
    for container in containers:
        resources = container.get("resources", {})
        limit = resources.get("limits", {}).get("nvidia.com/gpu")
        request = resources.get("requests", {}).get("nvidia.com/gpu", limit)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 16
            or request != limit
        ):
            raise ValueError(
                "Whole-GPU limit must be an integer 1–16; explicit request must equal the limit."
            )
        total += limit
    return {
        "gpus": total,
        "valid": True,
        "note": "Teaching validation, not a Kubernetes admission server. Requires a configured GPU device plugin in a real cluster.",
    }


def parse_metrics(text):
    """Bounded reader for numeric Prometheus samples; no remote fetching or evaluation."""
    if len(text.encode()) > 200_000:
        raise ValueError("Upload at most 200 KB of metric text.")
    samples = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = re.fullmatch(
            r"([a-zA-Z_:][\w:]*)(\{[^\n]*\})?\s+([-+\d.eE]+)(?:\s+\d+)?", line.strip()
        )
        if not match:
            raise ValueError("Expected numeric Prometheus exposition samples.")
        value = float(match[3])
        if not math.isfinite(value):
            raise ValueError("Use finite samples in this beginner exercise.")
        samples.append({"metric": match[1], "labels": match[2] or "{}", "value": value})
    if not samples:
        raise ValueError("No numeric samples found.")
    return samples
