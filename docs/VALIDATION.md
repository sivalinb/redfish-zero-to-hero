# Validation evidence

Recorded on 2026-10-01 from the implementation workspace. Results below describe software behavior; they do not measure human learning or production readiness.

## Local automated checks

- Python 3.12.14; all direct runtime and development dependency versions are pinned in the requirements files.
- `ruff check .`: passed.
- `pytest -q`: **52 passed**, 0 failures, 0 errors, 0 skipped. Final recorded duration: 5.87 seconds.
- All 11 practical levels are exercised on both included scenario variants.
- Progress tests cover ownership, sequential locking, persistence, retry-safe awards, and the complete 11-badge / 1,100-XP path.
- Streamlit interaction tests walk the learn → practical → assessment → badge → next-level flow and render every level's learning and practice views.
- Protocol and SDK tests cover real loopback HTTP, OTLP protobuf decoding, propagated trace parents, metrics and logs, bounded parsing, permission checks, and invalid optional-router fallback.
- Synthetic router data preparation: 126 training rows, 36 validation rows, 54 seed families; family-level split is checked to prevent templated-family overlap.

The repositories share a tested learning engine, so their test counts are not three independent learning-outcome studies.

## Tutor regression measurements

`python -m scripts.evaluate_tutor --check` passed on 31 versioned authored synthetic cases (26 retrieval, 3 boundary, 2 unknown-topic cases).

| Measurement | Result |
|---|---:|
| Expected concept in retrieved top three | 100% |
| Included boundary cases handled | 100% |
| Included unknown topics handled | 100% |
| Retrieved answers contain course citations | 100% |
| Median local tutor latency | 1.890 ms |

[Machine-readable results](tutor-evaluation.json) record the measurement scope. This small authored set does not establish general retrieval accuracy, answer faithfulness, live language-model quality, or learner improvement.

## Browser and protocol observations

In the browser I advanced the animated server explanation, ran the Level 0 inventory experiment, answered five questions, and observed a real Server Explorer badge and 100 XP persisted in the progress view.

The browser screenshots are actual interface captures: [lesson](assets/lesson.jpg), [experiment](assets/lab.jpg). Animations were inspected through changing states; Play/Pause and step controls were exercised. The authored architecture illustration is a conceptual overview, not a runtime screenshot.

The HTTP tests start a real loopback server. Redfish inventory follows resource links, uses a demo session, and closes it. GPU exposition parsing checks field semantics against synthetic data. These protocol tests do not imply any connection to real equipment.

## Continuous integration and optional work

[GitHub Actions](https://github.com/sivalinb/redfish-zero-to-hero/actions/workflows/ci.yml) runs the Python suite on 3.11, 3.12, and 3.13, then builds and starts the Docker Compose stack and verifies its services through HTTP. Consult the actual run conclusion and README badge for the current published revision. Docker was not installed on the local development machine, so local Docker execution is not claimed.

The optional LoRA training, merge, served router, and generated-tutor quality experiments were not run on this Mac. Dataset preparation and invalid-response boundaries were tested. No trained weights, model-quality gain, real-GPU result, or human learner study is bundled.

See the [persona review](GRADUATE_REVIEW.md) and [independent transfer challenge](INDEPENDENT_CAPSTONE.md) for the interpretation of these checks.
