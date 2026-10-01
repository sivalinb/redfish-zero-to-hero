"""Learner-owned SQLite progress and server-side, idempotent mastery grading."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from .course import ROOT, course
from .labs import grade_lab


class ProgressStore:
    def __init__(self, path=None):
        self.path = Path(path or ROOT / ".runtime/progress.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS completions(profile TEXT, level INTEGER, score INTEGER,
                    completed TEXT, PRIMARY KEY(profile, level));
                CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, profile TEXT, level INTEGER,
                    questions TEXT, result TEXT, created TEXT);
                CREATE TABLE IF NOT EXISTS readings(profile TEXT, lesson TEXT, PRIMARY KEY(profile, lesson));
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @staticmethod
    def identity(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def create(self, name="Explorer"):
        token = secrets.token_urlsafe(32)
        identity = self.identity(token)
        with self.connect() as db:
            db.execute(
                "INSERT INTO profiles VALUES (?,?)", (identity, name.strip()[:40] or "Explorer")
            )
        return identity, token

    def resume(self, token):
        if not isinstance(token, str) or len(token) > 100:
            return None
        identity = self.identity(token.strip())
        with self.connect() as db:
            return (
                identity
                if db.execute("SELECT 1 FROM profiles WHERE id=?", (identity,)).fetchone()
                else None
            )

    def summary(self, profile):
        with self.connect() as db:
            owner = db.execute("SELECT name FROM profiles WHERE id=?", (profile,)).fetchone()
            if not owner:
                raise PermissionError("Unknown learner")
            completions = [
                dict(r)
                for r in db.execute(
                    "SELECT level,score,completed FROM completions WHERE profile=? ORDER BY level",
                    (profile,),
                )
            ]
            readings = [
                r[0] for r in db.execute("SELECT lesson FROM readings WHERE profile=?", (profile,))
            ]
        completed = {r["level"] for r in completions}
        unlocked = next((i for i in range(11) if i not in completed), 10)
        return {
            "name": owner[0],
            "completed": completions,
            "unlocked": unlocked,
            "readings": readings,
            "xp": len(completed) * 100,
        }

    def mark_read(self, profile, lesson):
        self.summary(profile)
        valid = {c["id"] for level in course()["levels"] for c in level["concepts"]}
        if lesson not in valid:
            raise ValueError("Unknown concept")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO readings VALUES (?,?)", (profile, lesson))

    def start(self, profile, level):
        if not isinstance(level, int) or not 0 <= level <= self.summary(profile)["unlocked"]:
            raise PermissionError(
                "Earn the preceding level's badge first. You can preview every lesson."
            )
        with self.connect() as db:
            existing = db.execute(
                "SELECT * FROM attempts WHERE profile=? AND level=? AND result IS NULL ORDER BY created DESC LIMIT 1",
                (profile, level),
            ).fetchone()
            if existing:
                return dict(existing)
            questions = [q["id"] for q in course()["levels"][level]["questions"]]
            secrets.SystemRandom().shuffle(questions)
            attempt = {
                "id": secrets.token_hex(16),
                "profile": profile,
                "level": level,
                "questions": json.dumps(questions),
                "result": None,
                "created": datetime.now(timezone.utc).isoformat(),
            }
            db.execute(
                "INSERT INTO attempts VALUES (:id,:profile,:level,:questions,:result,:created)",
                attempt,
            )
            return attempt

    def grade(self, profile, attempt_id, answers, plan):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM attempts WHERE id=? AND profile=?", (attempt_id, profile)
            ).fetchone()
        if not row:
            raise PermissionError("This attempt belongs to another learner.")
        if row["result"]:
            return json.loads(row["result"])
        level = row["level"]
        if level > self.summary(profile)["unlocked"]:
            raise PermissionError("Level locked.")
        questions = {q["id"]: q for q in course()["levels"][level]["questions"]}
        feedback = []
        for ident in json.loads(row["questions"]):
            q = questions[ident]
            answer = answers.get(ident)
            correct = (
                isinstance(answer, int) and not isinstance(answer, bool) and answer == q["answer"]
            )
            feedback.append(
                {
                    "question": q["prompt"],
                    "correct": correct,
                    "explanation": q["explanation"],
                    "review": q.get("review", ""),
                }
            )
        score = round(100 * sum(f["correct"] for f in feedback) / len(feedback))
        lab = grade_lab(course()["kind"], level, plan)
        result = {
            "level": level,
            "score": score,
            "lab": lab,
            "passed": score >= 80 and lab["passed"],
            "feedback": feedback,
        }
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            saved = db.execute(
                "SELECT result FROM attempts WHERE id=? AND profile=?", (attempt_id, profile)
            ).fetchone()[0]
            if saved:
                return json.loads(saved)
            db.execute(
                "UPDATE attempts SET result=? WHERE id=? AND profile=?",
                (json.dumps(result), attempt_id, profile),
            )
            if result["passed"]:
                db.execute(
                    "INSERT OR IGNORE INTO completions VALUES (?,?,?,?)",
                    (profile, level, score, datetime.now(timezone.utc).isoformat()),
                )
        return result
