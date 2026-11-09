"""The events the computers report, taken over from the published history in
the shape its last version left them, held by id alone, beside the turns,
usage and departures made of them; 0.2's turns and usage are made from its
events as they are taken over."""

from __future__ import annotations

lazy import asyncio
lazy import json
lazy from typing import Any, cast

import bazaar_contrib_storage as s
from bazaar_contrib_schema import Dependency, Migration, ModuleSchema

lazy from .ledger import Tally, contact, fresh

# the rows one statement carries, their values well under the 32,766 that
# SQLite binds in a statement
PAGE = 500

EVENTS_V1 = s.Table(
    name="events",
    columns=(
        s.ColumnDefinition(name="id", type="integer", nullable=True, generated=True),
        s.ColumnDefinition(name="computer_id", type="text"),
        s.ColumnDefinition(name="run_id", type="text"),
        s.ColumnDefinition(name="seq", type="integer"),
        s.ColumnDefinition(name="agent_id", type="text", nullable=True),
        s.ColumnDefinition(name="event_name", type="text"),
        s.ColumnDefinition(name="thread_id", type="text", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer"),
        s.ColumnDefinition(name="received_at_ms", type="integer"),
        s.ColumnDefinition(name="payload", type="text"),
    ),
    primary_key=("id",),
    unique=(("computer_id", "run_id", "seq"),),
)
# 0.2's indexes, kept as they were
INDEXES = (
    s.Index(name="events_by_agent", columns=("computer_id", "agent_id", "id")),
    s.Index(
        name="events_by_name", columns=("computer_id", "event_name", "agent_id", "id")
    ),
    s.Index(
        name="events_by_thread",
        columns=("computer_id", "agent_id", "thread_id", "created_at_ms"),
    ),
)

BASELINE = Migration(
    version=1,
    name="events",
    operations=(
        s.CreateTable(table=EVENTS_V1),
        *(s.CreateIndex(table="events", index=index) for index in INDEXES),
    ),
)

EVENTS = s.Table(
    name="events",
    columns=(
        s.ColumnDefinition(name="id", type="integer", nullable=True, generated=True),
        s.ColumnDefinition(name="computer_id", type="text"),
        s.ColumnDefinition(name="run_id", type="text"),
        s.ColumnDefinition(name="seq", type="integer"),
        s.ColumnDefinition(name="agent_id", type="text", nullable=True),
        s.ColumnDefinition(name="event_name", type="text"),
        s.ColumnDefinition(name="thread_id", type="text", nullable=True),
        s.ColumnDefinition(name="created_at_ms", type="integer"),
        s.ColumnDefinition(name="received_at_ms", type="integer"),
        s.ColumnDefinition(name="payload", type="text"),
        s.ColumnDefinition(name="envelope", type="text", nullable=True),
        s.ColumnDefinition(name="session_id", type="text", nullable=True),
        s.ColumnDefinition(name="turn_id", type="text", nullable=True),
        s.ColumnDefinition(name="message_id", type="text", nullable=True),
        # 1 for a message that opened a conversation waiting to be let in
        s.ColumnDefinition(name="contact", type="integer", default=0),
    ),
    primary_key=("id",),
    unique=(("computer_id", "run_id", "seq"),),
)
TURNS = s.Table(
    name="turns",
    columns=(
        s.ColumnDefinition(name="computer_id", type="text"),
        s.ColumnDefinition(name="agent_id", type="text"),
        s.ColumnDefinition(name="turn_id", type="text"),
        s.ColumnDefinition(name="session_id", type="text", nullable=True),
        s.ColumnDefinition(name="thread_id", type="text", nullable=True),
        s.ColumnDefinition(name="channel", type="text", nullable=True),
        s.ColumnDefinition(name="target_kind", type="text", nullable=True),
        s.ColumnDefinition(name="connection", type="text", nullable=True),
        s.ColumnDefinition(name="model", type="text", nullable=True),
        s.ColumnDefinition(name="message_id", type="text", nullable=True),
        s.ColumnDefinition(name="started_at_ms", type="integer"),
        s.ColumnDefinition(name="ended_at_ms", type="integer", nullable=True),
        s.ColumnDefinition(name="outcome", type="text", nullable=True),
        s.ColumnDefinition(name="reason", type="text", nullable=True),
        s.ColumnDefinition(name="input_tokens", type="integer", default=0),
        s.ColumnDefinition(name="cached_tokens", type="integer", default=0),
        s.ColumnDefinition(name="output_tokens", type="integer", default=0),
        s.ColumnDefinition(name="cost_usd", type="real", nullable=True),
        *(
            s.ColumnDefinition(name=kind, type="integer", default=0)
            for kind in (
                "commands",
                "files",
                "changes",
                "pages",
                "searches",
                "tools",
                "tasks",
            )
        ),
        s.ColumnDefinition(name="step", type="text", nullable=True),
        s.ColumnDefinition(name="step_at_ms", type="integer", nullable=True),
    ),
    primary_key=("computer_id", "agent_id", "turn_id"),
)
USAGE = s.Table(
    name="usage",
    columns=(
        s.ColumnDefinition(name="id", type="integer", nullable=True, generated=True),
        s.ColumnDefinition(name="computer_id", type="text"),
        s.ColumnDefinition(name="agent_id", type="text"),
        s.ColumnDefinition(name="session_id", type="text"),
        s.ColumnDefinition(name="turn_id", type="text", nullable=True),
        s.ColumnDefinition(name="at_ms", type="integer"),
        s.ColumnDefinition(name="input_tokens", type="integer"),
        s.ColumnDefinition(name="cached_tokens", type="integer"),
        s.ColumnDefinition(name="output_tokens", type="integer"),
        s.ColumnDefinition(name="cost_usd", type="real", nullable=True),
        s.ColumnDefinition(name="total_input", type="integer"),
        s.ColumnDefinition(name="total_cached", type="integer"),
        s.ColumnDefinition(name="total_output", type="integer"),
        s.ColumnDefinition(name="total_cost", type="real", nullable=True),
    ),
    primary_key=("id",),
)
DEPARTURES = s.Table(
    name="departures",
    columns=(
        s.ColumnDefinition(name="computer_id", type="text"),
        s.ColumnDefinition(name="seen_ms", type="integer"),
    ),
    primary_key=("computer_id", "seen_ms"),
)

# 0.2's events that end a turn, and how
ENDED = {
    "runtime.turn.completed": "completed",
    "runtime.turn.failed": "failed",
    "runtime.turn.unknown": "failed",
    "runtime.turn.cancelled": "cancelled",
}


class _Backfill:
    """0.2's events as they are taken over, in the order they came: each
    with the session, turn and message its correlation names and whether it
    is a new contact, and the turns and usage they add up to; 0.2 relates a
    turn to the message behind it by the message's inbound_seq."""

    def __init__(self) -> None:
        self.turns: dict[tuple[str, str, str], dict[str, s.Scalar]] = {}
        # the last running totals of each runtime session
        self._totals: dict[tuple[str, str, str], Tally] = {}
        # what kind of conversation each message came in, by its seq
        self._kinds: dict[tuple[str, str, str], str | None] = {}

    def page(
        self, rows: tuple[s.Record, ...]
    ) -> tuple[list[dict[str, s.Scalar]], list[dict[str, s.Scalar]]]:
        events: list[dict[str, s.Scalar]] = []
        usage: list[dict[str, s.Scalar]] = []
        for row in rows:
            name = cast(str, row["event_name"])
            payload: dict[str, Any] = json.loads(cast(str, row["payload"]))
            correlation = payload.get("correlation") or {}
            metadata = payload.get("metadata") or {}
            seq = correlation.get("inbound_seq")
            session = correlation.get("runtime_session_id")
            turn = correlation.get("turn_id")
            message = correlation.get("outbound_message_id") or (
                None if seq is None else str(seq)
            )
            events.append(
                row
                | {
                    "envelope": None,
                    "session_id": session,
                    "turn_id": turn,
                    "message_id": message,
                    "contact": contact(name, metadata),
                }
            )
            computer = cast(str, row["computer_id"])
            agent = cast(str, row["agent_id"])
            at = cast(int, row["created_at_ms"])
            if name == "channel.inbound.persisted" and message is not None:
                self._kinds[computer, agent, message] = metadata.get("target_kind")
            if turn is None or agent is None:
                continue
            key = (computer, agent, turn)
            if name == "runtime.request.turn.started":
                self.turns.setdefault(
                    key,
                    fresh(
                        computer,
                        agent,
                        turn,
                        started_ms=at,
                        session_id=session,
                        thread_id=correlation.get("thread_id"),
                        channel=correlation.get("channel"),
                        target_kind=self._kinds.get((computer, agent, str(seq))),
                        message_id=None if seq is None else str(seq),
                    ),
                )
            elif name in ENDED:
                ended = self.turns.setdefault(
                    key,
                    fresh(
                        computer,
                        agent,
                        turn,
                        started_ms=at,
                        session_id=session,
                        thread_id=correlation.get("thread_id"),
                        channel=correlation.get("channel"),
                    ),
                )
                ended |= {
                    "ended_at_ms": at,
                    "outcome": ENDED[name],
                    "reason": payload.get("error_message"),
                }
            elif name == "tool_call.started" and key in self.turns:
                self.turns[key]["tools"] = cast(int, self.turns[key]["tools"]) + 1
            elif name == "usage.updated" and session is not None:
                total = metadata.get("total") or {}
                now = Tally(
                    (total.get("input_tokens") or 0)
                    + (total.get("cache_write_input_tokens") or 0),
                    total.get("cached_input_tokens") or 0,
                    total.get("output_tokens") or 0,
                    metadata.get("cost_usd"),
                )
                grown = now.since(self._totals.get((computer, agent, session)))
                self._totals[computer, agent, session] = now
                usage.append(
                    {
                        "computer_id": computer,
                        "agent_id": agent,
                        "session_id": session,
                        "turn_id": turn,
                        "at_ms": at,
                        **grown.row(),
                        **now.totals(),
                    }
                )
                if key in self.turns:
                    counted = self.turns[key]
                    counted |= {
                        "input_tokens": cast(int, counted["input_tokens"])
                        + grown.input,
                        "cached_tokens": cast(int, counted["cached_tokens"])
                        + grown.cached,
                        "output_tokens": cast(int, counted["output_tokens"])
                        + grown.output,
                        "cost_usd": counted["cost_usd"]
                        if grown.cost is None
                        else cast(float, counted["cost_usd"] or 0.0) + grown.cost,
                    }
        return events, usage


async def _second(session: s.Session) -> None:
    """The events gain their envelope, the session, turn and message they
    belong to, and whether each is a new contact, rebuilt so that 0.2's rows
    get theirs a page at a time, their ids going on above the highest ever
    given; the turns, usage and departures made of them begin, 0.2's made
    from its events."""
    # the highest id ever given, which SQLite keeps beside the table and the
    # rows left may fall short of; the rebuilt table starts without it
    given = await session.select(
        s.Select(
            table="sqlite_sequence",
            columns={"seq": s.Column(name="seq")},
            where=s.Compare(
                left=s.Column(name="name"), operator="=", right=s.Value(value="events")
            ),
        )
    )
    await session.apply_schema(
        (
            *(s.DropIndex(table="events", name=index.name) for index in INDEXES),
            s.RenameTable(table="events", name="_events_v1"),
            s.CreateTable(table=EVENTS),
            *(s.CreateIndex(table="events", index=index) for index in INDEXES),
            s.CreateIndex(
                table="events",
                index=s.Index(
                    name="events_by_turn",
                    columns=("computer_id", "agent_id", "turn_id", "id"),
                ),
            ),
            s.CreateIndex(
                table="events",
                index=s.Index(
                    name="events_by_message",
                    columns=("computer_id", "agent_id", "message_id"),
                ),
            ),
            s.CreateTable(table=TURNS),
            s.CreateIndex(
                table="turns",
                index=s.Index(
                    name="turns_by_start",
                    columns=("computer_id", "agent_id", "started_at_ms"),
                ),
            ),
            s.CreateTable(table=USAGE),
            s.CreateIndex(
                table="usage",
                index=s.Index(
                    name="usage_by_session",
                    columns=("computer_id", "agent_id", "session_id", "id"),
                ),
            ),
            s.CreateIndex(
                table="usage",
                index=s.Index(
                    name="usage_by_time", columns=("computer_id", "agent_id", "at_ms")
                ),
            ),
            s.CreateTable(table=DEPARTURES),
        )
    )
    if given:
        # a row at that id, taken away again, leaves it recorded for the new table
        await session.insert(
            s.Insert(
                table="events",
                rows=(
                    {
                        "id": given[0]["seq"],
                        "computer_id": "",
                        "run_id": "",
                        "seq": 0,
                        "event_name": "",
                        "created_at_ms": 0,
                        "received_at_ms": 0,
                        "payload": "",
                    },
                ),
            )
        )
        await session.delete(
            s.Delete(
                table="events",
                where=s.Compare(
                    left=s.Column(name="id"),
                    operator="=",
                    right=s.Value(value=given[0]["seq"]),
                ),
            )
        )
    backfill = _Backfill()
    last = 0
    while rows := await session.select(
        s.Select(
            table="_events_v1",
            where=s.Compare(
                left=s.Column(name="id"), operator=">", right=s.Value(value=last)
            ),
            order_by=(s.Sort(expression=s.Column(name="id")),),
            limit=PAGE,
        )
    ):
        events, usage = await asyncio.to_thread(backfill.page, rows)
        await session.insert(s.Insert(table="events", rows=tuple(events)))
        if usage:
            await session.insert(s.Insert(table="usage", rows=tuple(usage)))
        last = cast(int, rows[-1]["id"])
    turns = list(backfill.turns.values())
    for start in range(0, len(turns), PAGE):
        await session.insert(
            s.Insert(table="turns", rows=tuple(turns[start : start + PAGE]))
        )
    await session.apply_schema((s.DropTable(table="_events_v1"),))


MODULE = ModuleSchema(
    tables=("events", "turns", "usage", "departures", "_events_v1"),
    migrations=(BASELINE, Migration(version=2, name="activity", apply=_second)),
    baseline=BASELINE,
    predecessor=Dependency(package="bazaar-contrib-server-migrations", version=8),
)
