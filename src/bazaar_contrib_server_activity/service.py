"""ACTIVITY: the turns, receipts, counts and cursors of the agents an account
may see, as AGENTS gives them."""

from __future__ import annotations

lazy from bisect import bisect_right
lazy from collections.abc import Mapping, Sequence
lazy from dataclasses import replace
lazy from typing import cast

lazy from bazaar_contrib_harness import (
    ToolCall,
    ToolCallCompleted,
    ToolCallFailed,
    ToolCallStarted,
)
lazy from bazaar_contrib_server import Account
lazy from bazaar_contrib_server_agent import Agents

lazy from .contracts import Bucket, Calls, Receipt, Turn
lazy from .ledger import kind
lazy from .store import Store

# the messages sent, received and let in, which a conversation's page follows
MESSAGES = (
    "channel.inbound.persisted",
    "channel.outbound.pending",
    "channel.outbound.queued",
    "channel.outbound.sent",
    "channel.outbound.partial",
    "channel.outbound.failed",
    "channel.outbound.unknown",
    "channel.session.reviewed",
)


def buckets(
    starts: Sequence[int],
    turns: Mapping[int, int],
    usage: Mapping[int, tuple[int, int, int, float | None]],
) -> list[Bucket]:
    """The quarter hours, by their start, put into the buckets their starts
    fall in."""
    filled = [Bucket(start, 0, 0, 0, 0, None) for start in starts]
    for at, count in turns.items():
        index = bisect_right(starts, at) - 1
        filled[index] = replace(filled[index], turns=filled[index].turns + count)
    for at, (fresh, cached, output, spent) in usage.items():
        index = bisect_right(starts, at) - 1
        bucket = filled[index]
        filled[index] = replace(
            bucket,
            input=bucket.input + fresh,
            cached=bucket.cached + cached,
            output=bucket.output + output,
            cost=bucket.cost if spent is None else (bucket.cost or 0.0) + spent,
        )
    return filled


class Service:
    def __init__(self, store: Store, agents: Agents) -> None:
        self._store = store
        self._agents = agents

    async def _seen(
        self, account: Account, agent_id: str | None, online: bool = False
    ) -> list[tuple[str, str]]:
        """The agent given, or every agent, the account may see, each with
        its computer; only those whose computer is here when asked."""
        if agent_id is None:
            found = list(await self._agents.agents(account))
        else:
            one = await self._agents.agent(account, agent_id)
            found = [] if one is None else [one]
        return [
            (agent.computer_id, agent.id)
            for agent in found
            if agent.online or not online
        ]

    async def cursor(
        self,
        account: Account,
        *,
        agent_id: str | None = None,
        thread_id: str | None = None,
        messages: bool = False,
    ) -> int:
        return await self._store.latest(
            await self._seen(account, agent_id),
            names=MESSAGES if messages else None,
            thread_id=thread_id,
        )

    async def working(
        self,
        account: Account,
        *,
        agent_id: str | None = None,
        thread_id: str | None = None,
    ) -> Sequence[Turn]:
        return await self._store.turns(
            await self._seen(account, agent_id, online=True),
            running=True,
            thread_id=thread_id,
        )

    async def turns(
        self,
        account: Account,
        since_ms: int,
        until_ms: int,
        *,
        agent_id: str | None = None,
        thread_id: str | None = None,
    ) -> Sequence[Turn]:
        return await self._store.turns(
            await self._seen(account, agent_id),
            start_ms=since_ms,
            end_ms=until_ms,
            thread_id=thread_id,
        )

    async def receipts(
        self, account: Account, agent_id: str, message_ids: Sequence[str]
    ) -> Mapping[str, Receipt]:
        seen = await self._seen(account, agent_id)
        if not seen or not message_ids:
            return {}
        ((computer_id, _),) = seen
        events = await self._store.replies(computer_id, agent_id, message_ids)
        turns = await self._store.named(
            seen, sorted({event.turn_id or "" for event in events})
        )
        wanted = set(message_ids)
        # since the turn began or its reply before: when, and the calls made
        last = {turn.turn_id: (turn.started_ms, Calls()) for turn in turns.values()}
        started: dict[str, ToolCall] = {}
        receipts: dict[str, Receipt] = {}
        for event in events:
            turn_id = event.turn_id or ""
            at, calls = last.get(turn_id, (event.at_ms, Calls()))
            output = event.output
            if event.name == "harness.tool.started":
                if output is not None:
                    call = cast(ToolCallStarted, output.payload).call
                    started[call.call_id] = call
            elif event.name.startswith("harness.tool."):
                # one whose end was too large to report counts as it started
                call = (
                    started.get(event.context.get("call_id") or "")
                    if output is None
                    else cast(ToolCallCompleted | ToolCallFailed, output.payload).call
                )
                if call is not None:
                    counted = kind(call.detail)
                    last[turn_id] = (
                        at,
                        replace(calls, **{counted: getattr(calls, counted) + 1}),
                    )
            else:
                if event.message_id in wanted:
                    receipts[event.message_id or ""] = Receipt(
                        calls, max(0, event.at_ms - at) // 1000
                    )
                last[turn_id] = (event.at_ms, Calls())
        return receipts

    async def stats(
        self,
        account: Account,
        starts: Sequence[int],
        until_ms: int,
        *,
        agent_id: str | None = None,
    ) -> Sequence[Bucket]:
        turns, usage = await self._store.quarters(
            await self._seen(account, agent_id), starts[0], until_ms
        )
        return buckets(starts, turns, usage)
