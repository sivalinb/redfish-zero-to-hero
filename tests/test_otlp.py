"""Decode a real OTLP protobuf export received over loopback HTTP."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from scripts.http_demo import run


def test_otlp_http_export_reaches_receiver(monkeypatch):
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            request = ExportTraceServiceRequest()
            request.ParseFromString(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, request))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", f"http://127.0.0.1:{server.server_port}/v1/traces"
    )
    try:
        result = run(otlp_endpoint=f"http://127.0.0.1:{server.server_port}/v1/traces")
        assert result["http_status"] == 200
        spans = [
            span
            for _, request in received
            for resource in request.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
        ]
        assert len(spans) == 3
        assert {path for path, _ in received} == {"/v1/traces"}
        assert len({span.trace_id for span in spans}) == 1
        assert len([span for span in spans if span.parent_span_id]) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
