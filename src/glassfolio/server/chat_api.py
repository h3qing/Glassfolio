"""Chat endpoints. The conversation lives in the page; the server keeps only
proposals awaiting the person's confirmation (bounded, in memory)."""

import time
from dataclasses import dataclass

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from glassfolio.assistant.agent import Proposal, respond
from glassfolio.assistant.tools import Context
from glassfolio.flows import list_inbox, resolve_flow
from glassfolio.lake import Lake, new_id
from glassfolio.server.api import keep_recent
from glassfolio.server.serialize import to_json
from glassfolio.settings import configured_model

MAX_MESSAGE = 4000


PROPOSAL_TTL_S = 30 * 60


@dataclass(frozen=True)
class PendingAction:
    proposal: Proposal
    created: float  # time.monotonic()


def _execute(lake: Lake, p: Proposal) -> str:
    if p.tool == "answer_question":
        a = p.args
        open_ids = {i.item_id for i in list_inbox(lake)}
        if a["item_id"] not in open_ids or (a.get("paired_item_id") and a["paired_item_id"] not in open_ids):
            raise ValueError("this question was already answered; nothing was changed")
        return resolve_flow(lake, a["item_id"], a["classification"], a.get("paired_item_id"),
                            a.get("remember") is True, actor="model", reason=p.reason)
    raise ValueError(f"no confirmation handler for {p.tool}")


class ChatApi:
    def __init__(self, lake: Lake, today):
        self.lake = lake
        self.today = today
        self.pending: dict[str, PendingAction] = {}

    async def chat(self, request: Request) -> JSONResponse:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("expected a JSON object")
        message = str(body.get("message") or "").strip()[:MAX_MESSAGE]
        if not message:
            raise ValueError("type a question")
        model = configured_model()
        if model is None:
            return JSONResponse({"reply": "Choose a local model under Settings first; the assistant only uses "
                                          "a model running on this Mac.", "steps": [], "proposals": [], "cards": []})
        history = body.get("history") if isinstance(body.get("history"), list) else []
        turn = await run_in_threadpool(respond, model, Context(self.lake, self.today()), history, message)
        ids = [new_id("act") for _ in turn.proposals]
        now = time.monotonic()
        self.pending = keep_recent({**self.pending, **{i: PendingAction(p, now) for i, p in zip(ids, turn.proposals)}})
        return JSONResponse(to_json({
            "reply": turn.reply, "model": model.name, "steps": turn.steps, "cards": list(turn.cards),
            "ungrounded": list(turn.ungrounded), "false_claim": turn.false_claim,
            "proposals": [{"action_id": i, "summary": p.summary, "reason": p.reason}
                          for i, p in zip(ids, turn.proposals)]}))

    async def confirm(self, request: Request) -> JSONResponse:
        body = await request.json()
        action_id = str(body.get("action_id")) if isinstance(body, dict) else ""
        pending = self.pending.get(action_id)
        self.pending = {k: v for k, v in self.pending.items() if k != action_id}
        if pending is None or time.monotonic() - pending.created > PROPOSAL_TTL_S:
            raise ValueError("this proposal expired; ask again")
        return JSONResponse({"op_id": await run_in_threadpool(_execute, self.lake, pending.proposal)})
