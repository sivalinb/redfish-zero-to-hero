"""Local HTTP training service; simulator state never controls real equipment."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from .course import course
from .redfish import Simulator, exposition as redfish_metrics
from .gpu import replay, exposition as gpu_metrics


def handler_factory(sim, mode="redfish", scenario="healthy"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log session tokens or request bodies.

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request()

        def do_PATCH(self):
            self.handle_request()

        def do_DELETE(self):
            self.handle_request()

        def send(self, status, body, content_type="application/json", headers=None):
            payload = (json.dumps(body) if content_type == "application/json" else body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

        def handle_request(self):
            if self.command == "GET" and self.path == "/health":
                return self.send(200, {"ready": True, "mode": mode, "simulation": True})
            if self.command == "GET" and self.path == "/metrics":
                text = gpu_metrics(replay(scenario)) if mode == "gpu" else redfish_metrics(sim)
                return self.send(200, text, "text/plain; version=0.0.4")
            if mode != "redfish":
                return self.send(
                    404, {"error": "The GPU replay server exposes /health and /metrics only."}
                )
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 16_000:
                    return self.send(413, {"error": "Training requests are limited to 16 KB."})
                body = json.loads(self.rfile.read(length)) if length else {}
                if not isinstance(body, dict):
                    raise ValueError("Expected JSON object")
                result = sim.request(
                    self.command,
                    self.path,
                    body,
                    self.headers.get("X-Auth-Token"),
                    self.headers.get("X-Training-Confirm") == "yes",
                )
                self.send(result.status, result.body, headers=result.headers)
            except (ValueError, TypeError):
                self.send(400, {"error": "Invalid training request."})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8631)
    parser.add_argument(
        "--mode",
        choices=["redfish", "gpu"],
        default="gpu" if course()["kind"] == "gpu" else "redfish",
    )
    parser.add_argument("--scenario", default="healthy")
    parser.add_argument(
        "--container",
        action="store_true",
        help="Bind container interfaces; publish ports to loopback only.",
    )
    args = parser.parse_args()
    if args.mode == "gpu":
        replay(args.scenario)
        sim = Simulator()
    else:
        sim = Simulator(args.scenario)
    server = ThreadingHTTPServer(
        ("0.0.0.0" if args.container else "127.0.0.1", args.port),
        handler_factory(sim, args.mode, args.scenario),
    )
    print(
        f"Training {args.mode} service on port {args.port}. No real hardware operations.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
