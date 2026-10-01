import json
from pathlib import Path
import pytest
from academy.course import course, resolve_pointer
from academy.labs import grade_lab
from academy.progress import ProgressStore


def test_curriculum_has_complete_beginner_path():
    data = course()
    assert [level["number"] for level in data["levels"]] == list(range(11))
    assert sum(len(level["concepts"]) for level in data["levels"]) == 33
    seen = set()
    for level in data["levels"]:
        assert len(level["questions"]) == 5
        assert level["objective"] and level["lab"]["hints"] and level["badge"]
        assert set(level["prerequisites"]) <= set(data["glossary"])
        for concept in level["concepts"]:
            assert concept["id"] not in seen
            seen.add(concept["id"])
            assert len(concept["explanation"].split()) >= 60
            assert set(concept["terms"]) <= set(data["glossary"])
            assert concept["analogy"] and concept["example"] and concept["mistake"]
            frames = concept["animation"]["frames"]
            assert len(frames) == 4 and len({f["narration"] for f in frames}) == 4
            assert len({json.dumps(f["nodes"]) for f in frames}) > 1
        for q in level["questions"]:
            assert len(q["options"]) == 4 and 0 <= q["answer"] < 4 and q["explanation"]


@pytest.mark.parametrize("level", range(11))
def test_each_practical_solution_transfers_to_both_scenarios(level):
    data = course()
    result = grade_lab(data["kind"], level, data["levels"][level]["lab"]["solution"])
    assert result["passed"], result


def test_client_cannot_submit_a_success_flag_instead_of_a_plan():
    result = grade_lab(course()["kind"], 0, {"passed": True, "score": 100, "badge": True})
    assert not result["passed"]


def test_badges_require_real_plan_and_quiz(tmp_path):
    store = ProgressStore(tmp_path / "progress.sqlite3")
    identity, token = store.create("New graduate")
    assert store.resume(token) == identity
    assert store.resume("not-the-code") is None
    with pytest.raises(PermissionError):
        store.start(identity, 1)
    attempt = store.start(identity, 0)
    q = course()["levels"][0]["questions"]
    answers = {question["id"]: question["answer"] for question in q}
    failed = store.grade(identity, attempt["id"], answers, {"passed": True})
    assert failed["score"] == 100 and not failed["passed"]
    assert store.summary(identity)["xp"] == 0
    retry = store.start(identity, 0)
    result = store.grade(identity, retry["id"], answers, course()["levels"][0]["lab"]["solution"])
    assert result["passed"]
    assert store.grade(identity, retry["id"], {}, {}) == result
    assert store.summary(identity)["xp"] == 100
    assert store.summary(identity)["unlocked"] == 1
    assert ProgressStore(tmp_path / "progress.sqlite3").resume(token) == identity


def test_profile_cannot_grade_someone_elses_attempt(tmp_path):
    store = ProgressStore(tmp_path / "p.sqlite3")
    first, _ = store.create()
    second, _ = store.create()
    attempt = store.start(first, 0)
    with pytest.raises(PermissionError):
        store.grade(second, attempt["id"], {}, {})


def test_all_levels_can_be_earned_without_duplicate_awards(tmp_path):
    store = ProgressStore(tmp_path / "all.sqlite3")
    identity, _ = store.create()
    for level in course()["levels"]:
        attempt = store.start(identity, level["number"])
        answers = {q["id"]: q["answer"] for q in level["questions"]}
        result = store.grade(identity, attempt["id"], answers, level["lab"]["solution"])
        assert result["passed"], result
        assert store.grade(identity, attempt["id"], {}, {})["passed"]
    summary = store.summary(identity)
    assert summary["xp"] == 1100 and len(summary["completed"]) == 11


def test_pointer_escapes_and_arrays():
    assert resolve_pointer({"a/b": [{"~key": 4}]}, "/a~1b/0/~0key") == 4
    with pytest.raises(ValueError):
        resolve_pointer({}, "__import__('os')")


def test_animation_has_keyboard_and_reduced_motion_support():
    text = (Path(__file__).parents[1] / "academy/components/animation.html").read_text()
    assert "prefers-reduced-motion" in text and "ArrowRight" in text and "aria-live" in text
    assert "textContent" in text and "eval(" not in text
