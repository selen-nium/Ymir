"""User box reviews (corrected labels), stored in SQLite so the detector team can retrain on them.

Objects follow the stoahackjunction annotation workbench schema (id, class, bbox_xyxy, subtype, status, proposal_id).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import settings


class ReviewObject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    class_: str = Field(alias="class", min_length=1, max_length=64)
    bbox_xyxy: list[float] = Field(min_length=4, max_length=4)
    subtype: str = Field(default="", max_length=120)
    status: Literal["accepted", "rejected"]
    proposal_id: str | None = Field(default=None, max_length=64)

    @field_validator("bbox_xyxy")
    @classmethod
    def positive(cls, b):
        if b[2] <= b[0] or b[3] <= b[1] or min(b) < 0:
            raise ValueError("box must have positive width and height")
        return b


class Review(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_: Literal["drawing-qa-review-v1"] = Field(alias="schema")
    drawing: dict
    question: str = Field(max_length=2000)
    class_: str = Field(alias="class", min_length=1, max_length=64)
    user_count: int = Field(ge=0)
    model: dict
    objects: list[ReviewObject] = Field(max_length=2000)
    reviewed_at: str


class ReviewStore:
    def __init__(self, path: Path = settings.DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT, drawing_id TEXT NOT NULL, class TEXT NOT NULL,
                user_count INTEGER NOT NULL, model_count INTEGER, record TEXT NOT NULL, created_at TEXT NOT NULL)""")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path)
        try:
            with db:  # commits on success
                yield db
        finally:
            db.close()

    def add(self, review: Review) -> int:
        record = review.model_dump(by_alias=True, exclude_none=True)
        with self.lock, self._db() as db:
            cur = db.execute("INSERT INTO reviews (drawing_id, class, user_count, model_count, record, created_at) VALUES (?,?,?,?,?,?)",
                             (str(review.drawing.get("id", "")), review.class_, review.user_count, review.model.get("value"),
                              json.dumps(record, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
            return cur.lastrowid

    def list(self, drawing_id: str | None = None) -> list[dict]:
        query, args = "SELECT id, record, created_at FROM reviews", ()
        if drawing_id:
            query, args = query + " WHERE drawing_id = ?", (drawing_id,)
        with self._db() as db:
            rows = db.execute(query + " ORDER BY id", args).fetchall()
        return [{"id": i, "created_at": t, **json.loads(r)} for i, r, t in rows]
