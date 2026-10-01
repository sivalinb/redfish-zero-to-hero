"""Real OpenTelemetry SDK spans, W3C context, metrics, and correlated logs.

Services run in-process in the UI experiment. A separate HTTP example demonstrates
actual process/network boundaries. Failure conditions are synthetic; SDK timings
measure the Python execution, not a production incident's latency.
"""

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry._logs import LogRecord, SeverityNumber
import time


def experiment(
    service="checkout",
    requests=3,
    propagate=True,
    sample_ratio=1.0,
    redact=True,
    scenario="healthy",
    batch_query=True,
    label_policy="bounded",
):
    if not isinstance(requests, int) or not 1 <= requests <= 20:
        raise ValueError("Choose 1–20 requests.")
    if (
        not isinstance(service, str)
        or not service.strip()
        or len(service) > 40
        or service in ("payment", "database")
    ):
        raise ValueError("Use a service name of 1–40 characters.")
    if not isinstance(sample_ratio, (int, float)) or not 0 <= sample_ratio <= 1:
        raise ValueError("Sampling probability must be between 0 and 1.")
    if scenario not in ("healthy", "database_error", "n_plus_one"):
        raise ValueError("Unknown scenario")
    if label_policy not in ("bounded", "request_id"):
        raise ValueError("Choose bounded or request_id metric labels.")
    if not all(isinstance(v, bool) for v in (propagate, redact, batch_query)):
        raise ValueError("Experiment switches must be boolean.")
    exporter = InMemorySpanExporter()
    providers = {}
    for name in (service, "payment", "database"):
        provider = TracerProvider(
            resource=Resource.create({"service.name": name}),
            sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
            shutdown_on_exit=False,
        )
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        providers[name] = provider
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(
        resource=Resource.create({"service.name": service}),
        metric_readers=[reader],
        shutdown_on_exit=False,
    )
    meter = meter_provider.get_meter("zero-to-hero.experiment")
    counter = meter.create_counter("checkout.requests", unit="{request}")
    latency = meter.create_histogram(
        "checkout.duration", unit="s", description="SDK experiment wall-clock duration"
    )
    log_exporter = InMemoryLogRecordExporter()
    log_provider = LoggerProvider(
        resource=Resource.create({"service.name": service}), shutdown_on_exit=False
    )
    log_provider.add_log_record_processor(SimpleLogRecordProcessor(log_exporter))
    logger = log_provider.get_logger("zero-to-hero.experiment")
    propagator = TraceContextTextMapPropagator()
    carriers = []
    try:
        for number in range(requests):
            start = time.perf_counter()
            frontend = providers[service].get_tracer("zero-to-hero")
            with frontend.start_as_current_span(
                "POST /checkout",
                context=Context(),
                kind=trace.SpanKind.SERVER,
                attributes={"http.request.method": "POST", "http.route": "/checkout"},
            ) as outer:
                carrier = {}
                if propagate:
                    propagator.inject(carrier)
                carriers.append(carrier.copy())
                payment_ctx = propagator.extract(carrier) if propagate else Context()
                with (
                    providers["payment"]
                    .get_tracer("zero-to-hero")
                    .start_as_current_span(
                        "charge", context=payment_ctx, kind=trace.SpanKind.SERVER
                    ) as payment
                ):
                    database_calls = 5 if scenario == "n_plus_one" and not batch_query else 1
                    for query_index in range(database_calls):
                        downstream = {}
                        if propagate:
                            propagator.inject(downstream)
                        db_ctx = propagator.extract(downstream) if propagate else Context()
                        with (
                            providers["database"]
                            .get_tracer("zero-to-hero")
                            .start_as_current_span(
                                "SELECT inventory",
                                context=db_ctx,
                                kind=trace.SpanKind.SERVER,
                                attributes={
                                    "db.system.name": "sqlite",
                                    "db.operation.name": "SELECT",
                                    "training.query_index": query_index,
                                },
                            ) as database
                        ):
                            if scenario == "database_error":
                                database.set_status(
                                    trace.Status(
                                        trace.StatusCode.ERROR, "Injected database failure"
                                    )
                                )
                                database.add_event(
                                    "exception",
                                    {
                                        "exception.type": "TrainingDatabaseError",
                                        "exception.message": "Injected timeout",
                                    },
                                )
                                payment.set_status(
                                    trace.Status(trace.StatusCode.ERROR, "Database unavailable")
                                )
                                outer.set_status(
                                    trace.Status(trace.StatusCode.ERROR, "Checkout failed")
                                )
                attributes = {"http.route": "/checkout", "training.request_number": number}
                if not redact:
                    attributes["user.email"] = "learner@example.test"
                logger.emit(
                    LogRecord(
                        timestamp=time.time_ns(),
                        context=trace.set_span_in_context(outer),
                        severity_number=SeverityNumber.ERROR
                        if scenario == "database_error"
                        else SeverityNumber.INFO,
                        severity_text="ERROR" if scenario == "database_error" else "INFO",
                        body="Checkout failed"
                        if scenario == "database_error"
                        else "Checkout completed",
                        attributes=attributes,
                    )
                )
                labels = {
                    "http.route": "/checkout",
                    "outcome": "error" if scenario == "database_error" else "success",
                }
                if label_policy == "request_id":
                    labels["training.request_id"] = str(number)
                counter.add(1, labels)
                latency.record(time.perf_counter() - start, labels)
        spans = [
            {
                "name": s.name,
                "service": s.resource.attributes["service.name"],
                "trace_id": f"{s.context.trace_id:032x}",
                "span_id": f"{s.context.span_id:016x}",
                "parent_id": f"{s.parent.span_id:016x}" if s.parent else None,
                "start_ns": s.start_time,
                "end_ns": s.end_time,
                "duration_ms": (s.end_time - s.start_time) / 1_000_000,
                "status": s.status.status_code.name,
                "attributes": dict(s.attributes),
                "events": [{"name": e.name, "attributes": dict(e.attributes)} for e in s.events],
            }
            for s in exporter.get_finished_spans()
        ]
        logs = [
            {
                "body": record.log_record.body,
                "severity": record.log_record.severity_text,
                "trace_id": f"{record.log_record.trace_id:032x}",
                "span_id": f"{record.log_record.span_id:016x}",
                "attributes": dict(record.log_record.attributes),
            }
            for record in log_exporter.get_finished_logs()
        ]
        metrics = []
        for resource_metrics in reader.get_metrics_data().resource_metrics:
            for scope in resource_metrics.scope_metrics:
                for metric in scope.metrics:
                    for point in metric.data.data_points:
                        item = {
                            "name": metric.name,
                            "unit": metric.unit,
                            "attributes": dict(point.attributes),
                        }
                        if hasattr(point, "value"):
                            item["value"] = point.value
                        else:
                            item.update(
                                count=point.count,
                                sum=point.sum,
                                bounds=list(point.explicit_bounds),
                                buckets=list(point.bucket_counts),
                            )
                        metrics.append(item)
        return {
            "mode": "real SDK; logical services in one Python process",
            "scenario": scenario,
            "spans": spans,
            "logs": logs,
            "metrics": metrics,
            "carriers": carriers,
            "requests": requests,
            "propagate": propagate,
            "redact": redact,
            "sample_ratio": sample_ratio,
        }
    finally:
        for provider in providers.values():
            provider.shutdown()
        meter_provider.shutdown()
        log_provider.shutdown()


def collector_config(redact=True, batch=True, memory_limit_mib=128):
    if not 64 <= memory_limit_mib <= 1024:
        raise ValueError("Use 64–1024 MiB for this training Collector.")
    processors = {
        "memory_limiter": {
            "check_interval": "1s",
            "limit_mib": memory_limit_mib,
            "spike_limit_mib": 32,
        }
    }
    pipeline = ["memory_limiter"]
    if redact:
        processors["attributes/redact"] = {"actions": [{"key": "user.email", "action": "delete"}]}
        pipeline.append("attributes/redact")
    if batch:
        processors["batch"] = {"timeout": "1s"}
        pipeline.append("batch")
    return {
        "receivers": {"otlp": {"protocols": {"http": {"endpoint": "127.0.0.1:4318"}}}},
        "processors": processors,
        "exporters": {"debug": {"verbosity": "detailed"}},
        "service": {
            "pipelines": {
                signal: {
                    "receivers": ["otlp"],
                    "processors": pipeline.copy(),
                    "exporters": ["debug"],
                }
                for signal in ("traces", "metrics", "logs")
            }
        },
    }


def validate_collector(config):
    if not isinstance(config, dict):
        raise ValueError("Collector configuration must be a YAML object.")
    declared = {
        name: set(config.get(name, {})) for name in ("receivers", "processors", "exporters")
    }
    pipelines = config.get("service", {}).get("pipelines", {})
    if not pipelines:
        raise ValueError("Define at least one service pipeline.")
    for name, pipeline in pipelines.items():
        for kind in declared:
            if not isinstance(pipeline.get(kind, []), list):
                raise ValueError(f"{name}.{kind} must be a list.")
            missing = set(pipeline.get(kind, [])) - declared[kind]
            if missing:
                raise ValueError(f"{name} references undeclared {kind}: {sorted(missing)}")
        if not pipeline.get("receivers") or not pipeline.get("exporters"):
            raise ValueError(f"{name} needs a receiver and an exporter.")
    return {
        "valid": True,
        "pipelines": list(pipelines),
        "note": "Structural teaching check. A real Collector validates component types and complete settings.",
    }
