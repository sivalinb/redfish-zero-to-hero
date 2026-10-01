"""Three real loopback HTTP services, instrumented with OpenTelemetry.

No hardware or external service is required. SQLite performs a real local query;
--failure injects a database exception. Optional OTLP export targets a configured
operator-owned Collector. Service boundaries use HTTP; processes share this CLI.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import argparse
import json
import os
import sqlite3
from urllib.parse import urlparse
import httpx
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


def run(failure=False, otlp_endpoint=None):
    if otlp_endpoint:
        parsed = urlparse(otlp_endpoint)
        if (
            parsed.scheme not in ("http", "https")
            or parsed.username
            or parsed.password
            or not parsed.hostname
        ):
            raise ValueError("Use an operator-owned OTLP endpoint without credentials in its URL.")
        if parsed.scheme == "http" and parsed.hostname not in (
            "localhost",
            "127.0.0.1",
            "::1",
            "collector",
        ):
            raise ValueError("Use HTTPS for a remote endpoint.")
    exporter = InMemorySpanExporter()
    propagator = TraceContextTextMapPropagator()
    providers, services, threads, addresses = {}, {}, [], {}
    for name in ("checkout", "payment", "database"):
        provider = TracerProvider(
            resource=Resource.create({"service.name": name}), shutdown_on_exit=False
        )
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        if otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(
                SimpleSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, timeout=3))
            )
        providers[name] = provider

    def handler(name):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path != "/work":
                    self.send_response(404)
                    self.end_headers()
                    return
                context = propagator.extract({k.lower(): v for k, v in self.headers.items()})
                with (
                    providers[name]
                    .get_tracer("zero-to-hero.http")
                    .start_as_current_span(
                        "GET /" + name,
                        context=context,
                        kind=trace.SpanKind.SERVER,
                        attributes={"http.request.method": "GET", "http.route": "/work"},
                    ) as span
                ):
                    status, data = 200, {"service": name}
                    try:
                        if name == "database":
                            if failure:
                                raise sqlite3.OperationalError("Injected training database error")
                            with sqlite3.connect(":memory:") as db:
                                data["value"] = db.execute("SELECT 1").fetchone()[0]
                        else:
                            downstream = "payment" if name == "checkout" else "database"
                            headers = {}
                            propagator.inject(headers)
                            result = httpx.get(
                                addresses[downstream] + "/work", headers=headers, timeout=3
                            )
                            result.raise_for_status()
                            data["downstream"] = result.json()
                    except (httpx.HTTPError, sqlite3.Error) as error:
                        span.record_exception(error)
                        span.set_status(trace.Status(trace.StatusCode.ERROR, type(error).__name__))
                        status, data = 502, {"service": name, "error": type(error).__name__}
                    payload = json.dumps(data).encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

        return Handler

    try:
        for name in providers:
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler(name))
            services[name] = server
            addresses[name] = "http://127.0.0.1:" + str(server.server_port)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            threads.append(thread)
        result = httpx.get(addresses["checkout"] + "/work", timeout=10)
        spans = [
            {
                "service": s.resource.attributes["service.name"],
                "name": s.name,
                "trace_id": f"{s.context.trace_id:032x}",
                "span_id": f"{s.context.span_id:016x}",
                "parent_id": f"{s.parent.span_id:016x}" if s.parent else None,
                "status": s.status.status_code.name,
                "duration_ms": (s.end_time - s.start_time) / 1e6,
            }
            for s in exporter.get_finished_spans()
        ]
        return {
            "mode": "Real loopback HTTP boundaries; three services in one CLI process",
            "http_status": result.status_code,
            "result": result.json(),
            "spans": spans,
            "otlp_configured": bool(otlp_endpoint),
        }
    finally:
        for server in services.values():
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)
        for provider in providers.values():
            provider.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failure", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.failure, os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")), indent=2))
