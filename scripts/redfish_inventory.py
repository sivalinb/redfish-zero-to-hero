"""Read-only HTTP client restricted to the local teaching simulator."""

from urllib.parse import urlparse
import argparse
import json
import httpx


def inventory_http(base="http://127.0.0.1:8631", retries=2):
    parsed = urlparse(base)
    if (
        parsed.hostname not in ("localhost", "127.0.0.1", "::1", "simulator")
        or parsed.scheme != "http"
        or parsed.username
        or parsed.password
    ):
        raise ValueError(
            "This example accepts the local teaching simulator only, not real BMC addresses."
        )
    if not isinstance(retries, int) or not 0 <= retries <= 3:
        raise ValueError("Choose 0–3 retries.")
    with httpx.Client(base_url=base, timeout=3, follow_redirects=False) as client:
        session = client.post(
            "/redfish/v1/SessionService/Sessions",
            json={"UserName": "viewer", "Password": "training-only"},
        )
        session.raise_for_status()
        client.headers["X-Auth-Token"] = session.headers["X-Auth-Token"]
        location = session.headers["Location"]

        def get(path):
            if not isinstance(path, str) or not path.startswith("/redfish/v1/") or "://" in path:
                raise ValueError("The simulator returned a link outside its training namespace.")
            for attempt in range(retries + 1):
                response = client.get(path)
                if response.status_code not in (429, 503) or attempt == retries:
                    response.raise_for_status()
                    return response.json()

        try:
            root = get("/redfish/v1/")
            members = get(root["Systems"]["@odata.id"])["Members"]
            result = []
            for member in members:
                path = member["@odata.id"]
                try:
                    data = get(path)
                    result.append(
                        {
                            "id": data["Id"],
                            "memory_gib": data["MemorySummary"]["TotalSystemMemoryGiB"],
                            "processors": data["ProcessorSummary"]["Count"],
                        }
                    )
                except httpx.HTTPError as error:
                    result.append({"path": path, "error": type(error).__name__})
            return result
        finally:
            client.delete(location, headers={"X-Training-Confirm": "yes"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8631")
    args = parser.parse_args()
    print(json.dumps(inventory_http(args.base), indent=2))
