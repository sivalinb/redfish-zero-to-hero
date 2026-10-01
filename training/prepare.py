"""Split original course seed families before generating routing variations."""

import json
from pathlib import Path
import random
from academy.course import course

LABELS = ("teach", "lab", "clarify")
SYSTEM = 'Classify a course question. Return JSON {"intent":"teach|lab|clarify"}. Do not answer or perform actions.'


def prepare(output=None):
    destination = Path(output or Path(__file__).parent / "data")
    destination.mkdir(parents=True, exist_ok=True)
    data = course()
    seeds = {
        "teach": ["Explain " + c["title"] for level in data["levels"] for c in level["concepts"]],
        "lab": ["Help interpret my lab evidence for " + level["title"] for level in data["levels"]],
        "clarify": [
            "Explain the thing",
            "What does that mean?",
            "Fix it",
            "Tell me something",
            "Which one should I use?",
            "I am lost",
            "Go on",
            "Help",
            "What about it?",
            "Do that",
        ],
    }
    splits = {"train": [], "validation": []}
    manifest = []
    rng = random.Random(42)
    for label, families in seeds.items():
        indexed = list(enumerate(families))
        rng.shuffle(indexed)
        cutoff = int(len(families) * 0.8)
        for position, (number, seed) in enumerate(indexed):
            split = "train" if position < cutoff else "validation"
            family = f"{label}-{number}"
            manifest.append({"family": family, "split": split, "label": label, "seed": seed})
            for question in (seed, "Please help: " + seed, "I am learning this course. " + seed):
                splits[split].append(
                    {
                        "system": SYSTEM,
                        "conversations": [
                            {"from": "human", "value": question},
                            {"from": "gpt", "value": json.dumps({"intent": label})},
                        ],
                    }
                )
    for name, rows in splits.items():
        (destination / f"{name}.json").write_text(json.dumps(rows, indent=2) + "\n")
    info = {
        f"academy_router_{name}": {
            "file_name": f"{name}.json",
            "formatting": "sharegpt",
            "columns": {"messages": "conversations", "system": "system"},
            "tags": {
                "role_tag": "from",
                "content_tag": "value",
                "user_tag": "human",
                "assistant_tag": "gpt",
            },
        }
        for name in splits
    }
    (destination / "dataset_info.json").write_text(json.dumps(info, indent=2) + "\n")
    (destination / "split_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print({name: len(rows) for name, rows in splits.items()})
    return manifest


if __name__ == "__main__":
    prepare()
