# Hands-on path after the guided UI

Start with the UI at Level 0. Explain each concept before using a command. Choose Practice lab, run a plan, inspect the exported evidence, and try the changed scenario. The assessment checks both scenarios and five questions.

## Python protocol examples

Activate `.venv`, then:

```bash
python -m academy.server --scenario transient
# In another terminal, with the same environment activated:
python -m scripts.redfish_inventory
```

A stateful Python simulator exposes a deliberately small Redfish-style API. Reads, demo sessions, roles, confirmations, advertised reset capabilities, pending BIOS changes, asynchronous tasks, and audit records have observable effects. IPMI commands map to the same teaching state; native IPMI transport is not implemented.

## Complete local stack

Run `docker compose up --build -d`, then from the activated environment run:

```bash
python -m scripts.verify_stack
```

This verifies the HTTP training target and a successful Prometheus scrape. Container CI runs the same verifier with internal service URLs. Inspect `compose.yaml` for exact ports and configuration.

The HTTP training target is at http://127.0.0.1:8631; inspect `/health` and `/metrics`. Prometheus is at http://127.0.0.1:9091. Query `up{job="training"}` and preserve metric meanings and units. The GPU exporter exposes a final synthetic sample; application-rate benchmarking needs a changing real workload.



## Take the next step with evidence

Read a DMTF mockup or a supervised vendor BMC using its current documentation. Compare advertised links, schema versions, allowed ResetTypes, BIOS apply times, task messages, TLS, sessions, and event delivery. Practice real writes only in an appropriate lab and maintenance process.

The simulator is not a Redfish conformance implementation, vendor emulator, firmware installer, or hardware repair tool. Its Thermal representation is a teaching example; newer platforms may expose newer subsystem resources. `/training/` maintenance routes and message IDs are our own teaching fixtures. Task progress advances on reads. IPMI is a bounded comparison parser, not `ipmitool` execution.

Keep a record of what you directly observed, what the teaching model assumed, and what remains unknown. A troubleshooting conclusion should follow the same device or request through time and verify the relevant outcome.
