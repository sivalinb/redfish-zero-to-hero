"""Verify the optional local/container teaching stack through real HTTP."""

import argparse
import json
import time
from urllib.parse import urlparse
import httpx
from academy.course import course
from scripts.redfish_inventory import inventory_http as inventory
from scripts.http_demo import run


def wait(url):
    for _ in range(30):
        try:
            result = httpx.get(url, timeout=2)
            if result.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError("Teaching service did not become ready: " + urlparse(url).netloc)


def main():
    kind = course()["kind"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--service-url", default="http://127.0.0.1:" + ("8633" if kind == "gpu" else "8631")
    )
    parser.add_argument(
        "--prometheus-url", default="http://127.0.0.1:" + ("9093" if kind == "gpu" else "9091")
    )
    parser.add_argument("--collector-url", default="http://127.0.0.1:4318")
    args = parser.parse_args()
    if kind == "otel":
        wait(args.collector_url + "/v1/traces")
        result = run(otlp_endpoint=args.collector_url + "/v1/traces")
        assert result["http_status"] == 200 and len(result["spans"]) == 3
        assert len({span["trace_id"] for span in result["spans"]}) == 1
        print(json.dumps(result, indent=2))
        return
    wait(args.service_url + "/health")
    if kind == "redfish":
        rows = inventory(args.service_url)
        assert sorted(row["memory_gib"] for row in rows) == [32, 64]
    else:
        text = httpx.get(args.service_url + "/metrics", timeout=2).text
        assert "DCGM_FI_DEV_GPU_TEMP" in text
    wait(args.prometheus_url + "/-/ready")
    for _ in range(30):
        response = httpx.get(
            args.prometheus_url + "/api/v1/query", params={"query": 'up{job="training"}'}, timeout=2
        )
        response.raise_for_status()
        result = response.json()["data"]["result"]
        if result and result[0]["value"][1] == "1":
            print(json.dumps({"course": kind, "synthetic_training_target_up": result}, indent=2))
            return
        time.sleep(1)
    raise RuntimeError("Prometheus did not successfully scrape the training target.")


if __name__ == "__main__":
    main()
