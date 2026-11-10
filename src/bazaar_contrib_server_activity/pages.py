"""The activity's pages: the sections of an agent's activity tab - its turns
in progress, today's turns and today's usage - the whole record under it,
the agents in each computer's stall and the market's feed on the overview;
each follows what it shows through the update bus, and only for agents
and computers the account may see."""

from __future__ import annotations

from dataclasses import dataclass
lazy from bisect import bisect_right
lazy from collections.abc import Mapping, Sequence
lazy from datetime import datetime, time, timedelta, tzinfo
lazy from time import time_ns
lazy from typing import Any, cast

lazy from bazaar_contrib_harness import ToolCall
lazy from bazaar_contrib_server import Account, Pages, Server
lazy from bazaar_contrib_server_access import Access
lazy from bazaar_contrib_server_agent import Agent, Agents
lazy from bazaar_contrib_server_computer import Computers
lazy from starlette.exceptions import HTTPException
lazy from starlette.requests import Request
lazy from starlette.responses import Response
lazy from starlette.routing import Route

lazy from .contracts import Turn
lazy from .service import buckets
lazy from .store import OVER, SENT, STARTED, Event, Store

VIEW = "agents.view"
# what a page of the record and the feed hold, as 0.2's record did
PAGE = 50
# where each event of the whole record comes from, 0.2's by its own names
SOURCES = {
    "harness.turn.requested": "agent",
    "harness.completed": "agent",
    "harness.failed": "agent",
    "harness.cancelled": "agent",
    "runtime.request.turn.started": "agent",
    "runtime.turn.completed": "agent",
    "runtime.turn.failed": "agent",
    "runtime.turn.unknown": "agent",
    "runtime.turn.cancelled": "agent",
    "channel.inbound.persisted": "channel",
    "channel.outbound.sent": "channel",
    "channel.outbound.partial": "channel",
    "channel.outbound.queued": "channel",
    "channel.outbound.failed": "channel",
    "channel.outbound.unknown": "channel",
    "channel.connection.connected": "channel",
    "channel.connection.failed": "channel",
    "harness.tool.completed": "runtime",
    "harness.tool.failed": "runtime",
    "harness.pool.banned": "runtime",
    "tool_call.completed": "runtime",
    "tool_call.failed": "runtime",
    "reminder.fired": "reminder",
}
# the record's lines marked failed
FAILED = frozenset(
    {
        "harness.failed",
        "runtime.turn.failed",
        "runtime.turn.unknown",
        "channel.outbound.failed",
        "channel.outbound.unknown",
        "channel.connection.failed",
        "harness.tool.failed",
        "harness.pool.banned",
        "tool_call.failed",
    }
)
# how the record narrows, by what its lines are
FILTERS = {
    "all": tuple(SOURCES),
    "turns": tuple(name for name, source in SOURCES.items() if source == "agent"),
    "channel": tuple(name for name, source in SOURCES.items() if source == "channel"),
    "runtime": tuple(name for name, source in SOURCES.items() if source == "runtime"),
    "reminder": ("reminder.fired",),
    "failed": tuple(name for name in SOURCES if name in FAILED),
}
# what the feed shows, as its query picks it
FED = (*STARTED, *SENT, "channel.inbound.persisted", "reminder.fired", *OVER)
# a stall's spark: the turns of each six hours of the last seven days
SPARK = 28


@dataclass(frozen=True, slots=True)
class Item:
    """One thing on the feed: what an agent did, or a computer leaving."""

    at_ms: int
    event: Event | None
    agent: Agent | None
    computer: str | None


def midnight(tz: tzinfo | None) -> datetime:
    """The start of today on the viewer's clock."""
    return datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)


class Views:
    def __init__(
        self,
        server: Server,
        pages: Pages,
        access: Access,
        agents: Agents,
        computers: Computers,
        store: Store,
    ) -> None:
        self._server = server
        self._pages = pages
        self._access = access
        self._agents = agents
        self._computers = computers
        self._store = store
        self.routes = [
            Route("/live/{ref}", self._live),
            Route("/today/{ref}", self._today),
            Route("/usage/{ref}", self._usage),
            Route("/log/{ref}", self._log),
            Route("/log/{ref}/rows", self._rows),
            Route("/output/{ref}/{id:int}", self._output),
            Route("/feed", self._feed),
            Route("/stall/{ref}", self._stall),
        ]

    async def _agent(self, request: Request) -> Agent:
        """The agent a page is about, for an account that may see it; any
        other is not there."""
        account: Account = request.state.account
        if not account.allows(VIEW):
            raise HTTPException(status_code=403)
        found = await self._server.refs.values([request.path_params["ref"]])
        agent = None if found is None else await self._agents.agent(account, found[0])
        if agent is None:
            raise HTTPException(status_code=404)
        return agent

    async def _running(self, agent: Agent) -> list[Turn]:
        """The agent's turns in progress, none while its computer is away."""
        if not agent.online:
            return []
        return await self._store.turns([(agent.computer_id, agent.id)], running=True)

    async def _state(self, agent: Agent, running: Sequence[Turn]) -> dict[str, object]:
        """What the agent's sections follow: its newest event, and the step
        each turn in progress is at."""
        return {
            "event": await self._store.latest([(agent.computer_id, agent.id)]),
            "running": [[turn.turn_id, turn.step, turn.step_ms] for turn in running],
        }

    async def _live(self, request: Request) -> Response:
        """The turns in progress: what started each, the calls so far and
        what it is doing now."""
        agent = await self._agent(request)
        running = await self._running(agent)
        pair = [(agent.computer_id, agent.id)]
        return await self._pages.render(
            request,
            "activity/live.html",
            agent=agent,
            running=running,
            steps=await self._store.steps(
                agent.computer_id, agent.id, [turn.turn_id for turn in running]
            ),
            messages=await self._store.messages(
                pair, [turn.message_id for turn in running if turn.message_id]
            ),
            names=await self._store.conversations(
                pair, [turn.thread_id for turn in running]
            ),
            now_ms=time_ns() // 1_000_000,
            seen=await self._state(agent, running),
        )

    async def _today(self, request: Request) -> Response:
        """The turns started today on the viewer's clock, newest first, with
        how many there were and how many failed."""
        agent = await self._agent(request)
        start = midnight(await self._pages.zone(request))
        pair = [(agent.computer_id, agent.id)]
        start_ms = int(start.timestamp() * 1000)
        end_ms = int((start + timedelta(days=1)).timestamp() * 1000)
        shown = await self._store.turns(
            pair, start_ms=start_ms, end_ms=end_ms, limit=PAGE
        )
        total, failed = await self._store.tally(pair, start_ms, end_ms)
        return await self._pages.render(
            request,
            "activity/today.html",
            agent=agent,
            turns=shown,
            total=total,
            failed=failed,
            messages=await self._store.messages(
                pair, [turn.message_id for turn in shown if turn.message_id]
            ),
            names=await self._store.conversations(
                pair, [turn.thread_id for turn in shown]
            ),
            now_ms=time_ns() // 1_000_000,
            seen=await self._state(agent, await self._running(agent)),
        )

    async def _usage(self, request: Request) -> Response:
        """Today's tokens, their cost and cache hits, and each hour of the
        viewer's day."""
        agent = await self._agent(request)
        start = midnight(await self._pages.zone(request))
        starts = [
            int(start.replace(hour=hour).timestamp() * 1000) for hour in range(24)
        ]
        turns, usage = await self._store.quarters(
            [(agent.computer_id, agent.id)],
            starts[0],
            int((start + timedelta(days=1)).timestamp() * 1000),
        )
        hours = buckets(starts, turns, usage)
        return await self._pages.render(
            request,
            "activity/usage.html",
            agent=agent,
            hours=hours,
            peak=max(hour.input + hour.cached + hour.output for hour in hours),
            current=bisect_right(starts, time_ns() // 1_000_000) - 1,
            seen=await self._state(agent, await self._running(agent)),
        )

    def _filter(self, request: Request) -> str:
        chosen = request.query_params.get("filter", "all")
        return chosen if chosen in FILTERS else "all"

    async def _lines(self, agent: Agent, events: Sequence[Event]) -> dict[str, Any]:
        """What the record's lines need beside their events: the turns they
        speak of, the conversations they were in, the models of the runtime
        connections, which connections came back after failing, and each
        call as whole as it was reported."""
        computer_id, agent_id = agent.computer_id, agent.id
        pair = [(computer_id, agent_id)]
        models = {
            provider.connection: str(provider.values["model"])
            for provider in agent.config.harness
            if provider.values.get("model")
        }
        if any(
            event.name in ("harness.turn.requested", "harness.pool.banned")
            for event in events
        ):
            models |= await self._store.models(computer_id, agent_id)
        connected = [
            event.id for event in events if event.name == "channel.connection.connected"
        ]
        back: set[int] = set()
        if connected:
            failed: dict[str, bool] = {}
            for event in await self._store.connections(
                computer_id, agent_id, max(connected)
            ):
                connection = event.context.get("connection") or ""
                if event.name == "channel.connection.connected" and failed.get(
                    connection
                ):
                    back.add(event.id)
                failed[connection] = event.name == "channel.connection.failed"
        calls: dict[int, ToolCall] = {}
        cut = [
            event
            for event in events
            if event.name in ("harness.tool.completed", "harness.tool.failed")
            and event.omitted
        ]
        started = {
            step.call.call_id: step.call
            for steps in (
                await self._store.steps(
                    computer_id,
                    agent_id,
                    sorted({event.turn_id or "" for event in cut}),
                )
            ).values()
            for step in steps
        }
        for event in events:
            if event.name not in ("harness.tool.completed", "harness.tool.failed"):
                continue
            output = event.output
            call = (
                started.get(event.context.get("call_id") or "")
                if output is None
                else cast(Any, output.payload).call
            )
            if call is not None:
                calls[event.id] = call
        return {
            "turns": await self._store.named(
                pair,
                sorted({event.turn_id or "" for event in events if event.name in OVER}),
            ),
            "names": await self._store.conversations(
                pair, [event.thread_id for event in events]
            ),
            "models": models,
            "back": back,
            "calls": calls,
            "sources": SOURCES,
            "failures": FAILED,
        }

    async def _log(self, request: Request) -> Response:
        """The whole record, its first page, as the filter narrows it; or,
        for a turn, the page starting at the turn's newest line, its lines
        marked, as a conversation opens at a message the search found."""
        agent = await self._agent(request)
        chosen = self._filter(request)
        turn = request.query_params.get("turn")
        newest = (
            None
            if not turn
            else await self._store.newest(
                agent.computer_id, agent.id, FILTERS[chosen], turn
            )
        )
        events = await self._store.log(
            agent.computer_id,
            agent.id,
            FILTERS[chosen],
            before=None if newest is None else newest + 1,
            limit=PAGE + 1,
        )
        return await self._pages.render(
            request,
            "activity/log.html",
            agent=agent,
            chosen=chosen,
            filters=FILTERS,
            events=events[:PAGE],
            more=len(events) > PAGE,
            day=None,
            opened=None if newest is None else turn,
            seen=await self._state(agent, await self._running(agent)),
            **await self._lines(agent, events[:PAGE]),
        )

    async def _rows(self, request: Request) -> Response:
        """The record's lines: a page older than one, after the day it
        ended on; or, when what the record follows changed, every line from
        the oldest shown on, the newest at the top."""
        agent = await self._agent(request)
        chosen = self._filter(request)
        names = FILTERS[chosen]
        before = request.query_params.get("before")
        start = request.query_params.get("start")
        if start is None:
            events = await self._store.log(
                agent.computer_id,
                agent.id,
                names,
                before=None if before is None else int(before),
                limit=PAGE + 1,
            )
            more = len(events) > PAGE
            events = events[:PAGE]
        else:
            events = await self._store.log(
                agent.computer_id, agent.id, names, start=int(start)
            )
            more = bool(
                await self._store.log(
                    agent.computer_id, agent.id, names, before=int(start), limit=1
                )
            )
        return await self._pages.render(
            request,
            "activity/rows.html",
            agent=agent,
            chosen=chosen,
            events=events,
            more=more,
            day=request.query_params.get("day"),
            opened=request.query_params.get("turn"),
            seen=await self._state(agent, await self._running(agent)),
            **await self._lines(agent, events),
        )

    async def _output(self, request: Request) -> Response:
        """What a command printed, opened under its line."""
        agent = await self._agent(request)
        found = await self._store.one(
            agent.computer_id, agent.id, request.path_params["id"]
        )
        output = None if found is None else found.output
        if output is None:
            raise HTTPException(status_code=404)
        return await self._pages.render(
            request,
            "activity/output.html",
            call=cast(Any, output.payload).call,
        )

    async def _feed(self, request: Request) -> Response:
        """The latest of what the agents and computers the account sees did,
        newest first."""
        account: Account = request.state.account
        agents = {
            (agent.computer_id, agent.id): agent
            for agent in await self._agents.agents(account)
        }
        pairs = list(agents)
        events = await self._store.feed(pairs, PAGE)
        visible = await self._access.targets(account, "computer", "computers.view")
        computers = {
            computer.id: computer.name
            for computer in await self._computers.computers(
                None if visible is None else sorted(visible)
            )
        }
        items = sorted(
            [
                *(
                    Item(
                        event.at_ms,
                        event,
                        agents.get((event.computer_id, event.agent_id or "")),
                        None,
                    )
                    for event in events
                ),
                *(
                    Item(seen, None, None, computers[computer_id])
                    for computer_id, seen in await self._store.departures(
                        sorted(computers), PAGE
                    )
                ),
            ],
            key=lambda item: item.at_ms,
            reverse=True,
        )[:PAGE]
        shown = [item.event for item in items if item.event is not None]
        replied = [
            event.metadata.get("reply_to_message_id")
            for event in shown
            if event.name in SENT
        ]
        return await self._pages.render(
            request,
            "activity/feed.html",
            items=items,
            turns=await self._store.named(
                pairs,
                sorted({event.turn_id or "" for event in shown if event.name in OVER}),
            ),
            names=await self._store.conversations(
                pairs, [event.thread_id for event in shown]
            ),
            messages=await self._store.messages(
                pairs, [message for message in replied if message]
            ),
            seen=await self.fed(account, {}),
        )

    async def _computer(self, account: Account, short: str) -> str | None:
        """The computer a ref names, when the account may see it."""
        found = await self._server.refs.values([short])
        if found is None or not await self._access.can(
            account, "computer", found[0], "computers.view"
        ):
            return None
        return found[0]

    async def _stall(self, request: Request) -> Response:
        """The agents in a computer's stall that the account may see: each
        with its lantern, logos and what it is doing or last did."""
        account: Account = request.state.account
        computer_id = await self._computer(account, request.path_params["ref"])
        if computer_id is None:
            raise HTTPException(status_code=404)
        agents = [
            agent
            for agent in await self._agents.agents(account)
            if agent.computer_id == computer_id
        ]
        pairs = [(computer_id, agent.id) for agent in agents]
        await self._server.refs.load([agent.id for agent in agents])
        online = [
            pair for agent, pair in zip(agents, pairs, strict=True) if agent.online
        ]
        running = await self._store.turns(online, running=True)
        last = await self._store.last(
            computer_id, [agent.id for agent in agents], (*STARTED, *SENT)
        )
        now = time_ns() // 1_000_000
        today = midnight(await self._pages.zone(request))
        # six hours a point on the viewer's clock, the last the one now
        starts = [
            start
            for back in range(7, -1, -1)
            for hour in (0, 6, 12, 18)
            if (
                start := int(
                    datetime.combine(
                        today.date() - timedelta(days=back), time(hour), today.tzinfo
                    ).timestamp()
                    * 1000
                )
            )
            <= now
        ][-SPARK:]
        started = await self._store.started(
            computer_id, [agent.id for agent in agents], starts[0]
        )
        spark = {agent.id: [0] * len(starts) for agent in agents}
        for agent_id, quarters in started.items():
            for at, count in quarters.items():
                spark[agent_id][bisect_right(starts, at) - 1] += count
        return await self._pages.render(
            request,
            "activity/stall.html",
            agents=agents,
            running={turn.agent_id: turn for turn in reversed(running)},
            latest=last,
            names=await self._store.conversations(
                pairs,
                [turn.thread_id for turn in running]
                + [event.thread_id for event in last.values()],
            ),
            messages=await self._store.messages(
                pairs,
                [
                    message
                    for event in last.values()
                    if event.name in SENT
                    and (message := event.metadata.get("reply_to_message_id"))
                ],
            ),
            spark=spark,
            now_ms=now,
            seen=await self.stalled(account, {"computer": request.path_params["ref"]}),
        )

    async def followed(
        self, account: Account, scope: Mapping[str, str]
    ) -> Mapping[str, object] | None:
        """An agent's newest event and the steps of its turns in progress."""
        found = await self._server.refs.values([scope["agent"]])
        agent = None if found is None else await self._agents.agent(account, found[0])
        if agent is None:
            return None
        return await self._state(agent, await self._running(agent))

    async def fed(
        self, account: Account, scope: Mapping[str, str]
    ) -> Mapping[str, object] | None:
        """The newest of what the feed shows: an event of an agent the
        account sees, or a computer it sees leaving."""
        del scope
        pairs = [
            (agent.computer_id, agent.id)
            for agent in await self._agents.agents(account)
        ]
        visible = await self._access.targets(account, "computer", "computers.view")
        computers = await self._computers.computers(
            None if visible is None else sorted(visible)
        )
        left = await self._store.departures([computer.id for computer in computers], 1)
        return {
            "event": await self._store.latest(pairs, names=FED),
            "left": left[0][1] if left else 0,
        }

    async def stalled(
        self, account: Account, scope: Mapping[str, str]
    ) -> Mapping[str, object] | None:
        """The newest event of a computer's agents the account sees, the
        steps of their turns in progress, and whether each is here."""
        computer_id = await self._computer(account, scope["computer"])
        if computer_id is None:
            return None
        agents = [
            agent
            for agent in await self._agents.agents(account)
            if agent.computer_id == computer_id
        ]
        pairs = [(computer_id, agent.id) for agent in agents]
        running = await self._store.turns(
            [pair for agent, pair in zip(agents, pairs, strict=True) if agent.online],
            running=True,
        )
        return {
            "event": await self._store.latest(pairs),
            "running": [[turn.turn_id, turn.step] for turn in running],
            "online": sorted(agent.id for agent in agents if agent.online),
        }
