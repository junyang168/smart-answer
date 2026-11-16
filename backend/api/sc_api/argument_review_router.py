"""同工 review API: one question at a time for editors, rulings for the owner.

Identity comes from the signed headers the web proxy adds (``authenticated_actor``);
roles come from the site's config. Records live in the new knowledge database.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.pipeline import argument_review as review
from .roles import user_roles
from .transcript_review_router import authenticated_actor

REVIEWER_ROLES = {'editor', 'admin', 'beta_tester'}
OWNER_ROLES = {'admin'}


class Answer(BaseModel):
    question_id: str
    choice: str | None = None
    opinion: str | None = None


class Reply(BaseModel):
    followup_id: int
    choice: str | None = None
    text: str | None = None


class Ruling(BaseModel):
    question_id: str
    choice: str
    reason: str


def create_argument_review_router(root: Path, *, connect=review.connect):
    router = APIRouter(prefix='/argument_review')

    def roles(actor):
        config = json.loads((Path(root) / 'config/config.json').read_text())
        return user_roles(config, actor)

    async def reviewer(request: Request):
        actor = authenticated_actor(request, await request.body())
        found = roles(actor)
        if not found & REVIEWER_ROLES:
            raise HTTPException(403, '只有編輯同工可以審閱')
        return actor, found

    def errors(fn):
        try:
            return fn()
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0]) if exc.args else '找不到') from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get('/next')
    async def next_question(request: Request):
        actor, found = await reviewer(request)
        with connect() as conn:
            out = review.next_for(conn, actor)
            out['is_owner'] = bool(found & OWNER_ROLES)
            return out

    @router.post('/answer')
    async def answer(body: Answer, request: Request):
        actor, _ = await reviewer(request)
        with connect() as conn:
            answer_id = errors(lambda: review.submit_answer(conn, body.question_id, actor, choice=body.choice, opinion=body.opinion))
            return {'answer_id': answer_id, 'waiting_for_followup': bool((body.opinion or '').strip())}

    @router.post('/reply')
    async def reply(body: Reply, request: Request):
        actor, _ = await reviewer(request)
        with connect() as conn:
            errors(lambda: review.reply_followup(conn, body.followup_id, actor, choice=body.choice, text=body.text))
            return {'ok': True}

    @router.get('/mine')
    async def mine(request: Request):
        actor, _ = await reviewer(request)
        with connect() as conn:
            return {'answers': review.my_answers(conn, actor)}

    @router.get('/question/{question_id}')
    async def one(question_id: str, request: Request):
        await reviewer(request)
        with connect() as conn:
            return errors(lambda: review.question(conn, question_id))

    @router.get('/overview')
    async def overview(request: Request):
        actor, found = await reviewer(request)
        if not found & OWNER_ROLES:
            raise HTTPException(403, '只有負責人可以看全部回答')
        with connect() as conn:
            return {'questions': review.overview(conn)}

    @router.post('/rule')
    async def rule(body: Ruling, request: Request):
        actor, found = await reviewer(request)
        if not found & OWNER_ROLES:
            raise HTTPException(403, '只有負責人可以裁定')
        with connect() as conn:
            errors(lambda: review.rule(conn, body.question_id, actor, body.choice, body.reason))
            return {'ok': True}

    return router
