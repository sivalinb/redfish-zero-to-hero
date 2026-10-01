"""Measure actual local model predictions. This never substitutes invented training results."""

import argparse
import json
from pathlib import Path
import time
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from .prepare import SYSTEM, LABELS


def evaluate(model_path, output_path):
    data = json.loads((Path(__file__).parent / "data/validation.json").read_text())
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(
        model_path, torch_dtype="auto", device_map="auto", trust_remote_code=False
    )
    true, predicted, latencies = [], [], []
    for row in data:
        question = row["conversations"][0]["value"]
        true.append(json.loads(row["conversations"][1]["value"])["intent"])
        messages = [
            {
                "role": "system",
                "content": SYSTEM,
            },
            {"role": "user", "content": question},
        ]
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
        inputs = tokenizer(text, return_tensors="pt").to(model.device)
        started = time.perf_counter()
        with torch.inference_mode():
            tokens = model.generate(**inputs, max_new_tokens=100, do_sample=False)
        completion = tokenizer.decode(
            tokens[0][inputs.input_ids.shape[1] :], skip_special_tokens=True
        ).strip()
        latencies.append(time.perf_counter() - started)
        try:
            label = json.loads(completion)["intent"]
            if label not in LABELS:
                label = "invalid_output"
        except (ValueError, KeyError):
            label = "invalid_output"
        predicted.append(label)
    labels = sorted(set(true) | set(predicted))
    result = {
        "model": model_path,
        "n": len(true),
        "accuracy": accuracy_score(true, predicted),
        "classification_report": classification_report(
            true, predicted, zero_division=0, output_dict=True
        ),
        "labels": labels,
        "confusion_matrix": confusion_matrix(true, predicted, labels=labels).tolist(),
        "invalid_outputs": predicted.count("invalid_output"),
        "mean_latency_seconds": sum(latencies) / len(latencies),
        "predictions": predicted,
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("predictions", "classification_report")},
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    evaluate(args.model, args.output)
