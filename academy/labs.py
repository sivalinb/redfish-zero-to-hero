"""Each assessment re-runs a submitted plan against two independent scenarios.

No browser result, AI statement, or client-supplied score can award a badge.
Plans are bounded configuration, never arbitrary Python or shell execution.
"""

import json
from .course import resolve_pointer
from . import redfish, otel, gpu


def run_lab(kind, level, plan, variant=0):
    if not isinstance(plan, dict) or len(json.dumps(plan)) > 16_000:
        raise ValueError("Use a small configuration object.")
    if not isinstance(level, int) or not 0 <= level <= 10 or variant not in (0, 1):
        raise ValueError("Unknown lesson or scenario.")
    if kind == "redfish":
        return _redfish(level, plan, variant)
    if kind == "otel":
        return _otel(level, plan, variant)
    if kind == "gpu":
        return _gpu(level, plan, variant)
    raise ValueError("Unknown course.")


def grade_lab(kind, level, plan):
    reports = []
    for variant in (0, 1):
        try:
            result = run_lab(kind, level, plan, variant)
            reports.append(
                {"scenario": variant + 1, "passed": result["passed"], "message": result["message"]}
            )
        except (ValueError, KeyError, IndexError, TypeError) as error:
            reports.append({"scenario": variant + 1, "passed": False, "message": str(error)})
    return {"passed": all(r["passed"] for r in reports), "reports": reports}


def _redfish(level, plan, variant):
    scenario = "fan_failure" if variant else "healthy"
    if level == 1:
        scenario = "host_down" if variant else "healthy"
    if level == 9:
        scenario = "transient" if variant else "healthy"
    sim = redfish.Simulator(scenario)
    token = sim.login(plan.get("role", "viewer"))
    if level == 0:
        value = sim.request("GET", redfish.BASE + "/Systems/node-01", token=token).body
        passed = plan.get("component") == "Memory"
        return {
            "passed": passed,
            "message": "Memory holds the server's working data."
            if passed
            else "Choose the component used for temporary working data, rather than persistent storage or arithmetic.",
            "data": {
                "working_memory_gib": value["MemorySummary"]["TotalSystemMemoryGiB"],
                "cpu_sockets": value["ProcessorSummary"]["Count"],
                "selected_component": plan.get("component"),
            },
        }
    if level == 1:
        channel = plan.get("channel")
        response = sim.request("GET", redfish.BASE + "/Managers/bmc-1", token=token)
        passed = channel == "BMC" and response.status == 200
        return {
            "passed": passed,
            "message": "The management path works independently of the host OS in both scenarios."
            if passed
            else "The host operating system is unavailable in the second scenario. Use its management controller.",
            "data": {
                "host_power": sim.resources[redfish.BASE + "/Systems/node-01"]["PowerState"],
                "bmc": response.body,
                "selected_channel": channel,
            },
        }
    expected = [
        ("/Systems/node-01", "/MemorySummary/TotalSystemMemoryGiB"),
        ("/Managers/bmc-1", "/Status/Health"),
        ("/", "/RedfishVersion"),
        ("/", "/Systems/@odata.id"),
        ("/Systems/node-02", "/ProcessorSummary/Count"),
        ("/Chassis/chassis-1/Thermal", "/Fans/0/Status/Health"),
        ("/Managers/bmc-1/LogServices/EventLog/Entries", "/Members/0/MessageId"),
    ]
    if level <= 6:
        path, pointer = plan.get("path", redfish.BASE + "/"), plan.get("pointer", "")
        response = sim.request(plan.get("method", "GET"), path, token=token)
        if response.status != 200:
            return {
                "passed": False,
                "message": f"HTTP {response.status}: {response.body}",
                "data": response.body,
            }
        value = resolve_pointer(response.body, pointer)
        target, target_pointer = expected[level]
        target = redfish.BASE + target
        wanted = resolve_pointer(sim.resources[target], target_pointer)
        passed = (
            path.rstrip("/") == target.rstrip("/") and pointer == target_pointer and value == wanted
        )
        return {
            "passed": passed,
            "message": "You located the requested evidence."
            if passed
            else "The response is valid, but you selected a different resource or property. Follow the resource links and the lab goal.",
            "data": {
                "scenario": scenario,
                "http_status": response.status,
                "response": response.body,
                "selected_value": value,
            },
            "audit": sim.audit,
        }
    confirmed = plan.get("confirmed") is True
    if level == 7:
        result = sim.request(
            "POST",
            redfish.BASE + "/Systems/node-01/Actions/ComputerSystem.Reset",
            {"ResetType": plan.get("reset_type")},
            token,
            confirmed,
        )
        passed = (
            result.status == 200
            and sim.reset_count == 1
            and sim.resources[redfish.BASE + "/Systems/node-01"]["PowerState"] == "On"
        )
        data = {"action": result.body, "restart_count": sim.reset_count}
    elif level == 8:
        patch = sim.request(
            "PATCH",
            redfish.BASE + "/Systems/node-01/Bios/Settings",
            {"Attributes": {"BootMode": plan.get("boot_mode")}},
            token,
            confirmed,
        )
        before = deepcopy_dict(sim.resources[redfish.BASE + "/Systems/node-01/Bios"])
        restart = sim.request(
            "POST",
            redfish.BASE + "/Systems/node-01/Actions/ComputerSystem.Reset",
            {"ResetType": plan.get("reset_type")},
            token,
            confirmed,
        )
        after = sim.resources[redfish.BASE + "/Systems/node-01/Bios"]
        passed = (
            patch.status == 200
            and restart.status == 200
            and before["Attributes"]["BootMode"] == "Legacy"
            and after["Attributes"]["BootMode"] == "Uefi"
        )
        data = {"pending_change": patch.body, "before_restart": before, "after_restart": after}
    elif level == 9:
        data = redfish.inventory(sim, token, plan.get("retries", 0), plan.get("workers", 1))
        passed = len(data) == 2 and all("error" not in row for row in data)
    else:
        thermal_path = redfish.BASE + "/Chassis/chassis-1/Thermal"
        before = sim.request("GET", thermal_path, token=token).body
        failed = [
            fan
            for fan in before["Fans"]
            if fan["Status"]["Health"] == "Critical" and fan["Reading"] == 0
        ]
        action = None
        strategy = plan.get("strategy")
        if strategy == "replace_failed_fan" and failed:
            action = sim.request(
                "POST",
                "/training/v1/maintenance/replace-fan",
                {"MemberId": failed[0]["MemberId"]},
                token,
                confirmed,
            )
        elif strategy == "restart_every_server":
            action = sim.request(
                "POST",
                redfish.BASE + "/Systems/node-01/Actions/ComputerSystem.Reset",
                {"ResetType": "GracefulRestart"},
                token,
                confirmed,
            )
        after = sim.request("GET", thermal_path, token=token).body
        passed = (
            strategy == "replace_failed_fan"
            and not sim.reset_count
            and (
                not failed
                or (action.status == 200 and after["Fans"][0]["Status"]["Health"] == "OK")
            )
        )
        data = {
            "before": before,
            "after": after,
            "metrics": redfish.exposition(sim),
            "action": action.body if action else "No maintenance needed",
        }
    return {
        "passed": passed,
        "message": "The desired state was reached and verified."
        if passed
        else "The requested state was not reached. Inspect the response, permissions, confirmation, and before/after evidence.",
        "data": data,
        "audit": sim.audit,
    }


def deepcopy_dict(value):
    return json.loads(json.dumps(value))


def _otel(level, plan, variant):
    scenario = "healthy" if not variant else "database_error"
    if level == 9:
        scenario = "n_plus_one"
    cfg = {
        k: plan[k]
        for k in (
            "service",
            "requests",
            "propagate",
            "sample_ratio",
            "redact",
            "batch_query",
            "label_policy",
        )
        if k in plan
    }
    data = otel.experiment(scenario=scenario, **cfg)
    if level == 0:
        passed = plan.get("signal") == "trace" and len(data["spans"]) >= 3
    elif level == 1:
        passed = cfg.get("propagate") is True and any(s["parent_id"] for s in data["spans"])
    elif level == 2:
        passed = (
            cfg.get("propagate") is True
            and all("traceparent" in c for c in data["carriers"])
            and len({s["trace_id"] for s in data["spans"]}) == data["requests"]
        )
    elif level == 3:
        passed = cfg.get("service") == "checkout" and {s["service"] for s in data["spans"]} == {
            "checkout",
            "payment",
            "database",
        }
    elif level == 4:
        counter = sum(
            m.get("value", 0) for m in data["metrics"] if m["name"] == "checkout.requests"
        )
        hist = sum(m.get("count", 0) for m in data["metrics"] if m["name"] == "checkout.duration")
        passed = cfg.get("requests") == 5 and counter == 5 and hist == 5
    elif level == 5:
        failed = [s for s in data["spans"] if s["status"] == "ERROR" and s["events"]]
        passed = (
            plan.get("root_service") == "database"
            and cfg.get("propagate") is True
            and (not variant or any(s["service"] == "database" for s in failed))
        )
    elif level == 6:
        collector = otel.collector_config(
            plan.get("collector_redact", False),
            plan.get("collector_batch", False),
            plan.get("memory_limit_mib", 128),
        )
        otel.validate_collector(collector)
        passed = all(
            set(p["processors"]) == {"memory_limiter", "attributes/redact", "batch"}
            for p in collector["service"]["pipelines"].values()
        )
        data["collector"] = collector
    elif level == 7:
        passed = cfg.get("sample_ratio") == 0.2 and cfg.get("propagate") is True
    elif level == 8:
        points = [m for m in data["metrics"] if m["name"] == "checkout.requests"]
        passed = (
            cfg.get("redact") is True
            and cfg.get("label_policy") == "bounded"
            and all("user.email" not in log["attributes"] for log in data["logs"])
            and all("training.request_id" not in p["attributes"] for p in points)
        )
    elif level == 9:
        queries = [s for s in data["spans"] if s["service"] == "database"]
        passed = cfg.get("batch_query") is True and len(queries) == data["requests"]
    else:
        before = data
        if plan.get("remediation") == "restore_database":
            data = {"before": before, "after": otel.experiment(scenario="healthy", **cfg)}
        else:
            data = {"before": before, "after": before}
        after = data["after"]
        passed = (
            plan.get("remediation") == "restore_database"
            and cfg.get("propagate") is True
            and cfg.get("redact") is True
            and cfg.get("sample_ratio", 1.0) == 1
            and bool(after["spans"])
            and all(s["status"] != "ERROR" for s in after["spans"])
        )
    return {
        "passed": passed,
        "message": "Your SDK evidence satisfies the lab goal."
        if passed
        else "Inspect the trace, metric, or log evidence and adjust the experiment to satisfy the lab goal.",
        "data": data,
    }


def _gpu(level, plan, variant):
    scenario = ("healthy", "thermal")[variant]
    rows = gpu.replay(scenario)
    if level == 0:
        workers = plan.get("workers", 1)
        if not isinstance(workers, int) or not 1 <= workers <= 16:
            raise ValueError("Use 1–16 arithmetic workers.")
        tasks = 8 if not variant else 12
        data = {
            "independent_tasks": tasks,
            "workers": workers,
            "sequential_rounds": tasks,
            "parallel_rounds": math_ceil(tasks / workers),
            "note": "Equal-cost independent-task model; not measured GPU speed.",
        }
        passed = workers == 4 and plan.get("component") == "GPU"
    elif level in (1, 2):
        params, capacity = (7, 24) if not variant else (13, 48)
        data = gpu.memory_plan(
            params, plan.get("dtype", "FP32"), capacity, plan.get("batch", 1), 1, 2
        )
        if level == 1:
            passed = plan.get("dtype") == "FP16" and data["fits"]
        else:
            capacity = 24 if not variant else 20
            candidates = [gpu.memory_plan(7, "FP16", capacity, b, 1, 2) for b in range(1, 17)]
            best = max(
                (c for c in candidates if c["fits"]), key=lambda c: c["requests_gib"], default=None
            )
            batch = plan.get("batch_a" if not variant else "batch_b", 1)
            data = gpu.memory_plan(7, "FP16", capacity, batch, 1, 2)
            data["candidate_evidence"] = [
                {"batch": i + 1, "total_gib": c["total_gib"], "fits": c["fits"]}
                for i, c in enumerate(candidates)
            ]
            passed = (
                plan.get("strategy") == "largest_batch_that_fits"
                and data["fits"]
                and data["requests_gib"] == best["requests_gib"]
            )
    elif level == 3:
        rows = gpu.replay("memory_pressure" if variant else "healthy")
        samples = gpu.parse_metrics(gpu.exposition(rows))
        selected = next((s for s in samples if s["metric"] == plan.get("metric")), None)
        data = {
            "samples": samples,
            "selected": selected,
            "memory_used_percent": rows[-1]["memory_used_mib"] / rows[-1]["memory_total_mib"] * 100,
        }
        passed = (
            bool(selected)
            and selected["metric"] == "DCGM_FI_DEV_FB_USED"
            and plan.get("unit") == "MiB"
        )
    elif level in (4, 5, 6):
        scenario = (
            {4: "input_starved", 5: "thermal", 6: "gpu_error"}[level] if variant else "healthy"
        )
        rows = gpu.replay(scenario)
        diagnosis = gpu.diagnose(rows)
        correct = {
            4: "inspect_input_pipeline",
            5: "inspect_cooling",
            6: "isolate_and_collect_evidence",
        }[level]
        action = plan.get("remediation") if diagnosis != "healthy" else "no_change"
        passed = plan.get("remediation") == correct
        data = {
            "telemetry": rows,
            "diagnosis": diagnosis,
            "decision": action,
            "evidence": {"start": rows[0], "end": rows[-1]},
            "note": "A training decision, not a real hardware repair or performance result.",
        }
    elif level == 7:
        devices, capacity = (2, 10) if not variant else (4, 6)
        weights = gpu.memory_plan(7, "FP16", 24)["weights_gib"]
        per_gpu = weights / devices if plan.get("parallelism") == "model" else weights
        data = {
            "gpus": devices,
            "vram_per_gpu_gib": capacity,
            "weights_per_gpu_gib": per_gpu,
            "overhead_per_gpu_gib": 2,
            "fits": per_gpu + 2 <= capacity,
            "note": "Ideal even-sharding memory estimate; communication and actual framework partitions are additional constraints.",
        }
        passed = plan.get("parallelism") == "model" and data["fits"]
    elif level == 8:
        import yaml

        if not isinstance(plan.get("manifest"), str) or len(plan["manifest"]) > 6000:
            raise ValueError("Use a small Pod manifest.")
        try:
            parsed = yaml.safe_load(plan["manifest"])
        except yaml.YAMLError as error:
            raise ValueError("Invalid YAML manifest") from error
        validated = gpu.validate_manifest(parsed)
        data = gpu.scheduling(
            {"node-a": 2 if not variant else 1, "node-b": 1}, [validated["gpus"]] * 3
        )
        data["manifest"] = validated
        passed = validated["gpus"] == 1 and sum(
            a["state"] == "Assigned" for a in data["allocations"]
        ) == (3 if not variant else 2)
    elif level == 9:
        rows = gpu.replay("gpu_error" if variant else "healthy")
        text = gpu.exposition(rows)
        data = {
            "exposition": text,
            "parsed": gpu.parse_metrics(text),
            "xid_is_code": rows[-1]["xid_code"],
        }
        passed = (
            plan.get("xid_semantics") == "latest_error_code"
            and plan.get("request_semantics") == "cumulative_counter"
        )
    else:
        scenario = "memory_pressure" if not variant else "thermal"
        rows = gpu.replay(scenario)
        diagnosis = gpu.diagnose(rows)
        choice = (
            plan.get("memory_remediation")
            if diagnosis == "memory_pressure"
            else plan.get("thermal_remediation")
        )
        wanted = (
            "reduce_batch_and_verify_memory"
            if diagnosis == "memory_pressure"
            else "inspect_cooling_and_verify_clock"
        )
        passed = choice == wanted and plan.get("preserve_evidence") is True
        after = gpu.replay("healthy") if passed else rows
        data = {
            "before": rows,
            "diagnosis": diagnosis,
            "decision": choice,
            "after": after,
            "note": "The simulator models recovery. Actual remediation requires application and hardware validation.",
        }
    return {
        "passed": passed,
        "message": "Your calculation and evidence satisfy the scenario."
        if passed
        else "The evidence does not support this plan. Review the units, capacity, or diagnosis before trying again.",
        "data": data,
    }


def math_ceil(value):
    import math

    return math.ceil(value)
