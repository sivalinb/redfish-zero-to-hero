import json
from training.prepare import prepare, LABELS


def test_seed_families_split_before_variations(tmp_path):
    manifest = prepare(tmp_path)
    assert len(manifest) == 54
    assert len({row["family"] for row in manifest}) == 54
    train = json.loads((tmp_path / "train.json").read_text())
    validation = json.loads((tmp_path / "validation.json").read_text())
    assert len(train) == 126 and len(validation) == 36

    def questions(rows):
        return {row["conversations"][0]["value"] for row in rows}

    assert not questions(train) & questions(validation)
    for split in (train, validation):
        assert {json.loads(row["conversations"][1]["value"])["intent"] for row in split} == set(
            LABELS
        )
