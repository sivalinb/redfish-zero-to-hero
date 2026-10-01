# Optional Week 5 experiment: a small question router

The default application works without a language model. This separate GPU workflow trains a small LoRA adapter to classify questions as `teach`, `lab`, or `clarify`. Classification selects an explanation path; it cannot award badges, run commands, or operate equipment. Python permissions remain the boundary.

**Status:** dataset preparation is executed and tested. Training, merging, and model comparison are supplied as runnable workflows for a compatible GPU environment; no trained weights or improvement claim is bundled.

## Dataset and split

Run `python -m training.prepare` from the repository root. It creates 126 training rows and 36 validation rows from 54 original seed families: 33 concept questions, 11 lab questions, and 10 ambiguous requests. Seed families split before three templated variations are generated. The split manifest is saved. This is a small, imbalanced synthetic experiment, not a human learner benchmark. Hold out a separate human-authored test set before relying on a tuned router.

## Train, merge, and compare

Use a separate Python environment with compatible PyTorch, CUDA, and [LLaMA Factory](https://github.com/hiyouga/LLaMA-Factory), or use the included notebook on a GPU runtime. Follow its official [installation instructions](https://llamafactory.readthedocs.io/en/latest/getting_started/installation.html). Training dependencies do not belong in the lightweight UI environment.

```bash
python -m training.prepare
python -m training.evaluate --model Qwen/Qwen3-1.7B-Base --output training/data/baseline.json
llamafactory-cli train training/lora.yaml
llamafactory-cli export training/merge.yaml
python -m training.evaluate --model training/output/router-merged --output training/data/merged.json
```

The [Qwen3 1.7B base model](https://huggingface.co/Qwen/Qwen3-1.7B-Base) is a starting point for a narrow classification experiment. Rank-8 LoRA, alpha 16, 512-token inputs, FP16, batch one, accumulation eight, and three epochs are explicit experimental choices. Configuration follows the official [SFT](https://llamafactory.readthedocs.io/en/latest/getting_started/sft.html) and [merge](https://llamafactory.readthedocs.io/en/latest/getting_started/merge_lora.html) examples. Confirm GPU/framework compatibility before running. A QLoRA variant can add four-bit bitsandbytes quantization in a compatible environment; compare it separately.

The evaluator saves actual accuracy, per-class precision/recall/F1, a confusion matrix, invalid-output counts, predictions, and mean latency. Compare base and merged models on identical validation rows. Do not assume fine-tuning improves them.

## Optional runtime hook

After evaluation, serve the merged router using LLaMA Factory's local OpenAI-compatible API:

```bash
API_HOST=127.0.0.1 API_PORT=8000 llamafactory-cli api training/serve.yaml
ROUTER_BASE_URL=http://127.0.0.1:8000/v1 ROUTER_MODEL=training/output/router-merged python -m streamlit run app.py
```

Use the model ID advertised by your server. Invalid output, network errors, or absent configuration fall back to the deterministic router. Hard guardrails run first. No router output becomes a tool call. The ordinary tutor can independently use `TUTOR_OLLAMA_URL` and `TUTOR_MODEL` for generated explanations.
