from threading import Thread
from http.server import ThreadingHTTPServer
import pytest
import httpx
from academy.redfish import Simulator, BASE, inventory
from academy.server import handler_factory
from academy import otel, gpu
from scripts.http_demo import run as http_demo
from scripts.redfish_inventory import inventory_http


def test_redfish_read_auth_and_action_permissions():
    sim = Simulator()
    assert sim.request("GET", BASE + "/").status == 200
    assert sim.request("GET", BASE + "/Systems").status == 401
    viewer = sim.login("viewer")
    operator = sim.login("operator")
    target = BASE + "/Systems/node-01/Actions/ComputerSystem.Reset"
    assert sim.request("POST", target, {"ResetType": "GracefulRestart"}, viewer, True).status == 403
    assert sim.request("POST", target, {"ResetType": "GracefulRestart"}, operator).status == 409
    assert sim.reset_count == 0
    assert sim.request("POST", target, {"ResetType": "ForceRestart"}, operator, True).status == 400
    assert (
        sim.request("POST", target, {"ResetType": "GracefulRestart"}, operator, True).status == 200
    )
    assert sim.reset_count == 1
    assert (
        sim.request(
            "POST",
            BASE + "/Systems/node-02/Actions/ComputerSystem.Reset",
            {"ResetType": "GracefulRestart"},
            operator,
            True,
        ).status
        == 400
    )


def test_pending_settings_boot_override_and_task_completion():
    sim = Simulator()
    token = sim.login("operator")
    system = BASE + "/Systems/node-01"
    assert (
        sim.request(
            "PATCH", system + "/Bios/Settings", {"Attributes": {"BootMode": "Uefi"}}, token, True
        ).status
        == 200
    )
    assert sim.resources[system + "/Bios"]["Attributes"]["BootMode"] == "Legacy"
    boot = {"BootSourceOverrideTarget": "Pxe", "BootSourceOverrideEnabled": "Once"}
    assert sim.request("PATCH", system, {"Boot": boot}, token, True).status == 200
    sim.request(
        "POST",
        system + "/Actions/ComputerSystem.Reset",
        {"ResetType": "GracefulRestart"},
        token,
        True,
    )
    assert sim.resources[system + "/Bios"]["Attributes"]["BootMode"] == "Uefi"
    assert sim.resources[system]["LastBootTarget"] == "Pxe"
    assert sim.resources[system]["Boot"]["BootSourceOverrideEnabled"] == "Disabled"
    task = sim.request(
        "POST",
        BASE + "/UpdateService/Actions/UpdateService.SimpleUpdate",
        {"ImageURI": "training://firmware/demo-2.0"},
        token,
        True,
    )
    assert task.status == 202
    assert sim.request("GET", task.headers["Location"], token=token).body["TaskState"] == "Running"
    assert (
        sim.request("GET", task.headers["Location"], token=token).body["TaskState"] == "Completed"
    )
    assert sim.resources[BASE + "/UpdateService/FirmwareInventory/bmc"]["Version"] == "demo-2.0"


def test_ipmi_is_bounded_and_does_not_run_shell():
    sim = Simulator("fan_failure")
    viewer = sim.login()
    assert sim.ipmi("ipmitool sensor", viewer)["output"]["Fans"][0]["Reading"] == 0
    assert sim.ipmi("ipmitool sel list", viewer)["output"]["Members"][0]["Severity"] == "Critical"
    for command in ("sensor; touch /tmp/x", "cat /etc/passwd", "ipmitool -H real-bmc sensor"):
        with pytest.raises(ValueError):
            sim.ipmi(command, viewer)


def test_transient_inventory_read_uses_a_bounded_retry():
    sim = Simulator("transient")
    rows = inventory(sim, sim.login(), retries=0)
    assert rows[1]["error"] == 503
    sim = Simulator("transient")
    rows = inventory(sim, sim.login(), retries=1)
    assert [r["Id"] for r in rows] == ["node-01", "node-02"]
    with pytest.raises(ValueError):
        inventory(sim, sim.login(), workers=1000)


def test_actual_http_inventory_and_session_cleanup():
    sim = Simulator("transient")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(sim))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        rows = inventory_http(f"http://127.0.0.1:{server.server_port}")
        assert [r["memory_gib"] for r in rows] == [32, 64]
        assert not sim.sessions
        text = httpx.get(f"http://127.0.0.1:{server.server_port}/metrics").text
        assert "training_fan_speed_rpm" in text
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("propagate,expected", [(True, 2), (False, 6)])
def test_real_sdk_context_relationships(propagate, expected):
    data = otel.experiment(requests=2, propagate=propagate)
    assert len(data["spans"]) == 6
    assert len({s["trace_id"] for s in data["spans"]}) == expected
    if propagate:
        ids = {s["span_id"] for s in data["spans"]}
        assert all(s["parent_id"] in ids for s in data["spans"] if s["parent_id"])
        assert all("traceparent" in c for c in data["carriers"])
    assert all(log["trace_id"] != "0" * 32 for log in data["logs"])


def test_real_sdk_counter_histogram_and_logs():
    data = otel.experiment(requests=5, scenario="database_error")
    counter = [m for m in data["metrics"] if m["name"] == "checkout.requests"]
    histogram = [m for m in data["metrics"] if m["name"] == "checkout.duration"]
    assert sum(m["value"] for m in counter) == 5
    assert sum(m["count"] for m in histogram) == 5
    assert sum(sum(m["buckets"]) for m in histogram) == 5
    assert all(m["unit"] == "s" for m in histogram)
    assert all("user.email" not in log["attributes"] for log in data["logs"])
    assert all(s["status"] == "ERROR" for s in data["spans"])


def test_sampling_zero_and_one_have_expected_recording_boundaries():
    assert not otel.experiment(sample_ratio=0)["spans"]
    assert len(otel.experiment(sample_ratio=1)["spans"]) == 9
    assert otel.experiment(sample_ratio=0)["metrics"]


def test_cardinality_and_n_plus_one_experiments_change_actual_output():
    many = otel.experiment(requests=5, label_policy="request_id")
    bounded = otel.experiment(requests=5, label_policy="bounded")
    assert len([m for m in many["metrics"] if m["name"] == "checkout.requests"]) == 5
    assert len([m for m in bounded["metrics"] if m["name"] == "checkout.requests"]) == 1
    repeated = otel.experiment(requests=2, scenario="n_plus_one", batch_query=False)
    batched = otel.experiment(requests=2, scenario="n_plus_one", batch_query=True)
    assert len([s for s in repeated["spans"] if s["service"] == "database"]) == 10
    assert len([s for s in batched["spans"] if s["service"] == "database"]) == 2


@pytest.mark.parametrize("failure,status", [(False, 200), (True, 502)])
def test_real_loopback_http_has_one_connected_trace(failure, status):
    data = http_demo(failure)
    assert data["http_status"] == status
    assert len(data["spans"]) == 3
    assert len({s["trace_id"] for s in data["spans"]}) == 1
    assert {s["service"] for s in data["spans"]} == {"checkout", "payment", "database"}
    if failure:
        assert all(s["status"] == "ERROR" for s in data["spans"])


def test_collector_reference_validation_rejects_missing_components():
    config = otel.collector_config()
    assert otel.validate_collector(config)["valid"]
    config["service"]["pipelines"]["traces"]["processors"].append("not-declared")
    with pytest.raises(ValueError, match="undeclared"):
        otel.validate_collector(config)


def test_gpu_arithmetic_units_and_batch_boundaries():
    result = gpu.memory_plan(7, "FP16", 24, 8)
    assert result["weights_gib"] == pytest.approx(14e9 / 1024**3)
    assert result["fits"]
    assert not gpu.memory_plan(7, "FP16", 24, 9)["fits"]
    assert gpu.memory_plan(7, "FP16", 24, training=True)["total_gib"] > result["total_gib"]
    with pytest.raises(ValueError):
        gpu.memory_plan(float("nan"))


@pytest.mark.parametrize(
    "scenario", ["healthy", "memory_pressure", "thermal", "input_starved", "gpu_error"]
)
def test_gpu_replay_preserves_scenario_and_metric_semantics(scenario):
    rows = gpu.replay(scenario)
    assert gpu.diagnose(rows) == scenario
    samples = gpu.parse_metrics(gpu.exposition(rows))
    xid = next(s for s in samples if s["metric"] == "DCGM_FI_DEV_XID_ERRORS")
    assert xid["value"] == rows[-1]["xid_code"]
    assert "# TYPE DCGM_FI_DEV_XID_ERRORS gauge" in gpu.exposition(rows)


def test_gpu_whole_resource_pending_and_manifest_limits():
    result = gpu.scheduling({"a": 1, "b": 1}, [1, 1, 1])
    assert [r["state"] for r in result["allocations"]] == ["Assigned", "Assigned", "Pending"]
    with pytest.raises(ValueError):
        gpu.validate_manifest(
            {
                "apiVersion": "v1",
                "kind": "Pod",
                "spec": {"containers": [{"resources": {"limits": {"nvidia.com/gpu": 0.5}}}]},
            }
        )


def test_untrusted_metric_text_is_bounded():
    for text in ("no valid sample", "x NaN", "x " + "1" * 200_001):
        with pytest.raises(ValueError):
            gpu.parse_metrics(text)
