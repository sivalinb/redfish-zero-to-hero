from functools import lru_cache
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def course():
    data = json.loads((ROOT / "content/course.json").read_text())
    assert [level["number"] for level in data["levels"]] == list(range(11))
    return data


def glossary():
    return course()["glossary"]


def resolve_pointer(value, pointer):
    """RFC 6901 pointer, with bounded depth; no expression evaluation."""
    if pointer == "":
        return value
    if not pointer.startswith("/") or len(pointer) > 300:
        raise ValueError("Use a JSON pointer beginning with /, such as /Status/Health.")
    parts = pointer.split("/")[1:]
    if len(parts) > 16:
        raise ValueError("That pointer has too many steps.")
    for part in parts:
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value
