"""``POST /api/feedback``: record a thumbs up or down on an answer. Reviewed later with ``python -m identity_mcp.feedback``."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..feedback import store

router = APIRouter(tags=["chat"])


class StepIn(BaseModel):
    """One query behind the rated answer, as the chat page saw it. Limits keep a buggy client from sending a book."""
    tool: str = Field(max_length=40)
    question: str = Field(max_length=1000)
    sql: str = Field(default="", max_length=4000)
    linked: bool = False
    error: str = Field(default="", max_length=1000)


class FeedbackIn(BaseModel):
    """Body of ``POST /api/feedback``.

    Attributes:
        rating: ``up`` or ``down``.
        question: What the user asked.
        answer: The answer being rated.
        comment: Optional note, mostly for ``down`` ("what was wrong").
        mode: ``agent`` or ``single``.
        verified: Whether the answer passed the check against the data, if known.
        steps: The queries behind the answer (at most 8).
    """
    rating: Literal["up", "down"]
    question: str = Field(min_length=1, max_length=1000)
    answer: str = Field(max_length=6000)
    comment: str = Field(default="", max_length=1000)
    mode: Literal["agent", "single"] = "agent"
    verified: bool | None = None
    steps: list[StepIn] = Field(default_factory=list, max_length=8)


@router.post("/api/feedback", status_code=201)
def give_feedback(body: FeedbackIn):
    """Store one piece of feedback and return its id."""
    con = store.connect()
    try:
        feedback_id = store.add(
            con, rating=store.UP if body.rating == "up" else store.DOWN, question=body.question, answer=body.answer,
            comment=body.comment, mode=body.mode, verified=body.verified,
            steps=[store.Step(**s.model_dump()) for s in body.steps],
        )
    finally:
        con.close()
    return {"id": feedback_id}
