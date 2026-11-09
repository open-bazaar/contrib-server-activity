"""What the pages and other modules read of the activity: an agent's events a
page at a time, the turns, the calls of those running, the messages behind
them and the names of their conversations, usage by the quarter hour, the
latest of what every agent did and each computer's departures. Agents are
named with their computer, which leads every index."""

from __future__ import annotations

from dataclasses import dataclass
lazy import json
lazy from collections.abc import Iterable, Sequence
lazy from typing import Any, cast

lazy from bazaar_contrib_harness import HarnessOutput, ToolCall
lazy from bazaar_contrib_storage import (
    All,
    AnyOf,
    Binary,
    Call,
    Case,
    Column,
    Compare,
    Database,
    In,
    IsNull,
    Join,
    Predicate,
    Record,
    Select,
    Sort,
    Value,
)

from .contracts import Calls, Turn
lazy from .ledger import HARNESS, equals

# a quarter hour: every zone's offset is a whole number of them
QUARTER_MS = 900_000
# the kinds of tool call a turn counts, as its columns
KINDS = ("commands", "files", "changes", "pages", "searches", "tools", "tasks")
# the events a conversation is named by, newest first
NAMING = ("channel.inbound.persisted", "reminder.fired")
# a turn begun, a reply sent and a turn over, 0.2's by its own names
STARTED = ("harness.turn.requested", "runtime.request.turn.started")
SENT = ("channel.outbound.sent", "channel.outbound.partial", "channel.outbound.queued")
OVER = (
    "harness.completed",
    "harness.failed",
    "harness.cancelled",
    "runtime.turn.completed",
    "runtime.turn.failed",
    "runtime.turn.unknown",
    "runtime.turn.cancelled",
)


@dataclass(frozen=True, slots=True)
class Event:
    """An event as it was kept: where it belongs and what it carried; 0.2's
    have no envelope."""

    id: int
    computer_id: str
    agent_id: str | None
    name: str
    thread_id: str | None
    at_ms: int
    turn_id: str | None
    message_id: str | None
    payload: Any
    envelope: dict[str, Any]

    @property
    def context(self) -> dict[str, Any]:
        return self.envelope.get("context") or {}

    @property
    def error(self) -> str | None:
        """What went wrong, as the event says it."""
        failure = self.envelope.get("error")
        if failure is not None:
            return failure.get("message") or failure.get("kind")
        return self.payload.get("error_message") if self.legacy else None

    @property
    def legacy(self) -> bool:
        """Kept from 0.2, in its own shape: its words in correlation and
        metadata."""
        return not self.envelope

    @property
    def metadata(self) -> dict[str, Any]:
        return (
            (self.payload.get("metadata") or {})
            if isinstance(self.payload, dict)
            else {}
        )

    @property
    def omitted(self) -> bool:
        """Its payload was too large for a report and left out."""
        return isinstance(self.payload, dict) and self.payload.keys() == {"omitted"}

    @property
    def output(self) -> HarnessOutput | None:
        """A runtime's event as the harness contract has it."""
        if self.legacy or self.omitted or not self.name.startswith("harness."):
            return None
        return HARNESS.validate_python(self.payload)


@dataclass(frozen=True, slots=True)
class Conversation:
    """What a conversation is called: a group by its name, a private one by
    whom it is with."""

    kind: str | None
    name: str | None


@dataclass(frozen=True, slots=True)
class Step:
    """A tool call of a running turn: as it started, or whole once it ended,
    and whether it failed and why."""

    call: ToolCall
    ended: bool
    failed: bool
    error: str | None
    at_ms: int


def event(row: Record) -> Event:
    envelope = row.get("envelope")
    return Event(
        id=cast(int, row["id"]),
        computer_id=cast(str, row["computer_id"]),
        agent_id=cast(str | None, row["agent_id"]),
        name=cast(str, row["event_name"]),
        thread_id=cast(str | None, row["thread_id"]),
        at_ms=cast(int, row["created_at_ms"]),
        turn_id=cast(str | None, row.get("turn_id")),
        message_id=cast(str | None, row.get("message_id")),
        payload=json.loads(cast(str, row["payload"])),
        envelope={} if envelope is None else json.loads(cast(str, envelope)),
    )


def turn(row: Record) -> Turn:
    return Turn(
        computer_id=cast(str, row["computer_id"]),
        agent_id=cast(str, row["agent_id"]),
        turn_id=cast(str, row["turn_id"]),
        session_id=cast(str | None, row["session_id"]),
        thread_id=cast(str | None, row["thread_id"]),
        channel=cast(str | None, row["channel"]),
        target_kind=cast(str | None, row["target_kind"]),
        connection=cast(str | None, row["connection"]),
        model=cast(str | None, row["model"]),
        message_id=cast(str | None, row["message_id"]),
        started_ms=cast(int, row["started_at_ms"]),
        ended_ms=cast(int | None, row["ended_at_ms"]),
        outcome=cast(Any, row["outcome"]),
        reason=cast(str | None, row["reason"]),
        input=cast(int, row["input_tokens"]),
        cached=cast(int, row["cached_tokens"]),
        output=cast(int, row["output_tokens"]),
        cost=cast(float | None, row["cost_usd"]),
        calls=Calls(**{kind: cast(int, row[kind]) for kind in KINDS}),
        step=cast(Any, row["step"]),
        step_ms=cast(int | None, row["step_at_ms"]),
    )


def of(agents: Iterable[tuple[str, str]], table: str | None = None) -> Predicate:
    """The rows of the agents, each named with its computer."""
    by: dict[str, list[str]] = {}
    for computer_id, agent_id in agents:
        by.setdefault(computer_id, []).append(agent_id)
    return AnyOf(
        conditions=tuple(
            All(
                conditions=(
                    equals("computer_id", computer_id, table),
                    In(
                        expression=Column(name="agent_id", table=table),
                        values=tuple(agent_ids),
                    ),
                )
            )
            for computer_id, agent_ids in sorted(by.items())
        )
    )


def since(column: str, start_ms: int, end_ms: int) -> All:
    return All(
        conditions=(
            Compare(
                left=Column(name=column), operator=">=", right=Value(value=start_ms)
            ),
            Compare(left=Column(name=column), operator="<", right=Value(value=end_ms)),
        )
    )


class Store:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def _select(self, request: Select) -> tuple[Record, ...]:
        return await self._database.read(lambda session: session.select(request))

    async def latest(
        self,
        agents: Sequence[tuple[str, str]],
        *,
        names: Sequence[str] | None = None,
        thread_id: str | None = None,
    ) -> int:
        """The id of the newest event of the agents, of the names when given,
        in the conversation when one is; 0 for none."""
        if not agents:
            return 0
        conditions: list[Predicate] = [of(agents)]
        if names is not None:
            conditions.append(
                In(expression=Column(name="event_name"), values=tuple(names))
            )
        if thread_id is not None:
            conditions.append(equals("thread_id", thread_id))
        rows = await self._select(
            Select(
                table="events",
                columns={
                    "id": Call(
                        name="coalesce",
                        arguments=(
                            Call(name="max", arguments=(Column(name="id"),)),
                            Value(value=0),
                        ),
                    )
                },
                where=All(conditions=tuple(conditions)),
            )
        )
        return cast(int, rows[0]["id"])

    async def turns(
        self,
        agents: Sequence[tuple[str, str]],
        *,
        running: bool = False,
        start_ms: int | None = None,
        end_ms: int | None = None,
        thread_id: str | None = None,
        limit: int | None = None,
    ) -> list[Turn]:
        """The agents' turns still running, or started in the time; the
        running oldest first, the others newest first."""
        if not agents:
            return []
        conditions: list[Predicate] = [of(agents)]
        if running:
            conditions.append(IsNull(expression=Column(name="ended_at_ms")))
        if start_ms is not None and end_ms is not None:
            conditions.append(since("started_at_ms", start_ms, end_ms))
        if thread_id is not None:
            conditions.append(equals("thread_id", thread_id))
        rows = await self._select(
            Select(
                table="turns",
                where=All(conditions=tuple(conditions)),
                order_by=(
                    Sort(
                        expression=Column(name="started_at_ms"), descending=not running
                    ),
                ),
                limit=limit,
            )
        )
        return [turn(row) for row in rows]

    async def tally(
        self, agents: Sequence[tuple[str, str]], start_ms: int, end_ms: int
    ) -> tuple[int, int]:
        """How many turns the agents started in the time, and how many of
        them failed."""
        rows = await self._select(
            Select(
                table="turns",
                columns={
                    "turns": Call(name="count"),
                    "failed": Call(
                        name="coalesce",
                        arguments=(
                            Call(
                                name="sum",
                                arguments=(
                                    Case(
                                        when=equals("outcome", "failed"),
                                        then=Value(value=1),
                                        otherwise=Value(value=0),
                                    ),
                                ),
                            ),
                            Value(value=0),
                        ),
                    ),
                },
                where=All(
                    conditions=(of(agents), since("started_at_ms", start_ms, end_ms))
                ),
            )
        )
        return cast(int, rows[0]["turns"]), cast(int, rows[0]["failed"])

    async def named(
        self, agents: Sequence[tuple[str, str]], turn_ids: Sequence[str]
    ) -> dict[tuple[str, str], Turn]:
        """The agents' turns given, by agent and turn."""
        if not agents or not turn_ids:
            return {}
        rows = await self._select(
            Select(
                table="turns",
                where=All(
                    conditions=(
                        of(agents),
                        In(expression=Column(name="turn_id"), values=tuple(turn_ids)),
                    )
                ),
            )
        )
        return {
            (cast(str, row["agent_id"]), cast(str, row["turn_id"])): turn(row)
            for row in rows
        }

    async def steps(
        self, computer_id: str, agent_id: str, turn_ids: Sequence[str]
    ) -> dict[str, list[Step]]:
        """The tool calls of each turn given, in the order they started, as
        far as they went."""
        if not turn_ids:
            return {}
        rows = await self._select(
            Select(
                table="events",
                where=All(
                    conditions=(
                        equals("computer_id", computer_id),
                        equals("agent_id", agent_id),
                        In(expression=Column(name="turn_id"), values=tuple(turn_ids)),
                        In(
                            expression=Column(name="event_name"),
                            values=(
                                "harness.tool.started",
                                "harness.tool.completed",
                                "harness.tool.failed",
                            ),
                        ),
                    )
                ),
                order_by=(Sort(expression=Column(name="id")),),
            )
        )
        calls: dict[str, dict[str, Step]] = {turn_id: {} for turn_id in turn_ids}
        for kept in map(event, rows):
            output = kept.output
            ended = kept.name != "harness.tool.started"
            known = calls[cast(str, kept.turn_id)]
            call_id = cast(str, kept.context.get("call_id"))
            if output is None:
                # its end too large to report: as it started, over
                if call_id in known:
                    known[call_id] = Step(
                        known[call_id].call,
                        True,
                        kept.name == "harness.tool.failed",
                        kept.error,
                        known[call_id].at_ms,
                    )
                continue
            known[call_id] = Step(
                cast(Any, output.payload).call,
                ended,
                kept.name == "harness.tool.failed",
                kept.error,
                known[call_id].at_ms if call_id in known else kept.at_ms,
            )
        return {turn_id: list(steps.values()) for turn_id, steps in calls.items()}

    async def messages(
        self, agents: Sequence[tuple[str, str]], message_ids: Sequence[str]
    ) -> dict[tuple[str, str], Event]:
        """The messages that came in and the reminders that went off, by
        agent and message id."""
        if not agents or not message_ids:
            return {}
        rows = await self._select(
            Select(
                table="events",
                where=All(
                    conditions=(
                        of(agents),
                        In(
                            expression=Column(name="message_id"),
                            values=tuple(message_ids),
                        ),
                        In(expression=Column(name="event_name"), values=NAMING),
                    )
                ),
            )
        )
        return {
            (cast(str, found.agent_id), cast(str, found.message_id)): found
            for found in map(event, rows)
        }

    async def one(self, computer_id: str, agent_id: str, event_id: int) -> Event | None:
        rows = await self._select(
            Select(
                table="events",
                where=All(
                    conditions=(
                        equals("computer_id", computer_id),
                        equals("agent_id", agent_id),
                        equals("id", event_id),
                    )
                ),
            )
        )
        return event(rows[0]) if rows else None

    async def conversations(
        self, agents: Sequence[tuple[str, str]], thread_ids: Iterable[str | None]
    ) -> dict[str, Conversation]:
        """What each conversation given is called: a group by the name its
        newest message gives, a private one by who sent that; one with no
        message kept by the reminder that went off in it."""
        threads = tuple(sorted({one for one in thread_ids if one is not None}))
        if not agents or not threads:
            return {}
        newest = Select(
            table="events",
            columns={"id": Call(name="max", arguments=(Column(name="id"),))},
            where=All(
                conditions=(
                    of(agents),
                    In(expression=Column(name="thread_id"), values=threads),
                    In(expression=Column(name="event_name"), values=NAMING),
                )
            ),
            group_by=(Column(name="thread_id"), Column(name="event_name")),
        )
        rows = await self._select(
            Select(
                table="events",
                ctes={"newest": newest},
                joins=(
                    Join(
                        table="newest",
                        on=Compare(
                            left=Column(name="id", table="newest"),
                            operator="=",
                            right=Column(name="id", table="events"),
                        ),
                    ),
                ),
                columns={
                    name: Column(name=name, table="events")
                    for name in (
                        "id",
                        "computer_id",
                        "agent_id",
                        "event_name",
                        "thread_id",
                        "created_at_ms",
                        "payload",
                        "envelope",
                    )
                },
                order_by=(Sort(expression=Column(name="id", table="events")),),
            )
        )
        named: dict[str, Conversation] = {}
        for kept in map(event, rows):
            metadata = kept.metadata
            sender = metadata.get("sender") or {}
            if kept.name == "channel.inbound.persisted" or kept.thread_id not in named:
                named[cast(str, kept.thread_id)] = Conversation(
                    metadata.get("target_kind"),
                    metadata.get("target_name") or metadata.get("target")
                    if metadata.get("target_kind") == "group"
                    else sender.get("display_name")
                    or sender.get("name")
                    or sender.get("id"),
                )
        return named

    async def log(
        self,
        computer_id: str,
        agent_id: str,
        names: Sequence[str],
        *,
        before: int | None = None,
        start: int | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        """The agent's events of the names, newest first: those older than
        one, or every one from one on."""
        conditions: list[Predicate] = [
            equals("computer_id", computer_id),
            equals("agent_id", agent_id),
            In(expression=Column(name="event_name"), values=tuple(names)),
        ]
        if before is not None:
            conditions.append(
                Compare(left=Column(name="id"), operator="<", right=Value(value=before))
            )
        if start is not None:
            conditions.append(
                Compare(left=Column(name="id"), operator=">=", right=Value(value=start))
            )
        rows = await self._select(
            Select(
                table="events",
                where=All(conditions=tuple(conditions)),
                order_by=(Sort(expression=Column(name="id"), descending=True),),
                limit=limit,
            )
        )
        return [event(row) for row in rows]

    async def connections(
        self, computer_id: str, agent_id: str, until: int
    ) -> list[Event]:
        """The agent's channels connecting and failing up to an event,
        oldest first."""
        rows = await self._select(
            Select(
                table="events",
                where=All(
                    conditions=(
                        equals("computer_id", computer_id),
                        equals("agent_id", agent_id),
                        In(
                            expression=Column(name="event_name"),
                            values=(
                                "channel.connection.connected",
                                "channel.connection.failed",
                            ),
                        ),
                        Compare(
                            left=Column(name="id"),
                            operator="<=",
                            right=Value(value=until),
                        ),
                    )
                ),
                order_by=(Sort(expression=Column(name="id")),),
            )
        )
        return [event(row) for row in rows]

    async def models(self, computer_id: str, agent_id: str) -> dict[str, str]:
        """The model each runtime connection of the agent last reported."""
        latest = Select(
            table="turns",
            columns={
                "connection": Column(name="connection"),
                "started_at_ms": Call(
                    name="max", arguments=(Column(name="started_at_ms"),)
                ),
            },
            where=All(
                conditions=(
                    equals("computer_id", computer_id),
                    equals("agent_id", agent_id),
                    IsNull(expression=Column(name="model"), exclude=True),
                    IsNull(expression=Column(name="connection"), exclude=True),
                )
            ),
            group_by=(Column(name="connection"),),
        )
        rows = await self._select(
            Select(
                table="turns",
                ctes={"latest": latest},
                joins=(
                    Join(
                        table="latest",
                        on=All(
                            conditions=(
                                Compare(
                                    left=Column(name="connection", table="latest"),
                                    operator="=",
                                    right=Column(name="connection", table="turns"),
                                ),
                                Compare(
                                    left=Column(name="started_at_ms", table="latest"),
                                    operator="=",
                                    right=Column(name="started_at_ms", table="turns"),
                                ),
                            )
                        ),
                    ),
                ),
                columns={
                    "connection": Column(name="connection", table="turns"),
                    "model": Column(name="model", table="turns"),
                },
                where=All(
                    conditions=(
                        equals("computer_id", computer_id, "turns"),
                        equals("agent_id", agent_id, "turns"),
                    )
                ),
            )
        )
        return {cast(str, row["connection"]): cast(str, row["model"]) for row in rows}

    async def last(
        self, computer_id: str, agent_ids: Sequence[str], names: Sequence[str]
    ) -> dict[str, Event]:
        """The newest event of the names each agent of the computer has."""
        if not agent_ids:
            return {}
        agents = [(computer_id, agent_id) for agent_id in agent_ids]
        newest = Select(
            table="events",
            columns={"id": Call(name="max", arguments=(Column(name="id"),))},
            where=All(
                conditions=(
                    of(agents),
                    In(expression=Column(name="event_name"), values=tuple(names)),
                )
            ),
            group_by=(Column(name="agent_id"),),
        )
        rows = await self._select(
            Select(
                table="events",
                ctes={"newest": newest},
                joins=(
                    Join(
                        table="newest",
                        on=Compare(
                            left=Column(name="id", table="newest"),
                            operator="=",
                            right=Column(name="id", table="events"),
                        ),
                    ),
                ),
                columns={
                    name: Column(name=name, table="events")
                    for name in (
                        "id",
                        "computer_id",
                        "agent_id",
                        "event_name",
                        "thread_id",
                        "created_at_ms",
                        "turn_id",
                        "message_id",
                        "payload",
                        "envelope",
                    )
                },
            )
        )
        return {cast(str, found.agent_id): found for found in map(event, rows)}

    async def started(
        self, computer_id: str, agent_ids: Sequence[str], start_ms: int
    ) -> dict[str, dict[int, int]]:
        """How many turns each agent of the computer started in each quarter
        hour since then, by the quarter's start."""
        if not agent_ids:
            return {}
        quarter = Binary(
            operator="/",
            left=Column(name="started_at_ms"),
            right=Value(value=QUARTER_MS),
        )
        rows = await self._select(
            Select(
                table="turns",
                columns={
                    "agent_id": Column(name="agent_id"),
                    "quarter": quarter,
                    "turns": Call(name="count"),
                },
                where=All(
                    conditions=(
                        of([(computer_id, agent_id) for agent_id in agent_ids]),
                        Compare(
                            left=Column(name="started_at_ms"),
                            operator=">=",
                            right=Value(value=start_ms),
                        ),
                    )
                ),
                group_by=(Column(name="agent_id"), quarter),
            )
        )
        counted: dict[str, dict[int, int]] = {}
        for row in rows:
            counted.setdefault(cast(str, row["agent_id"]), {})[
                cast(int, row["quarter"]) * QUARTER_MS
            ] = cast(int, row["turns"])
        return counted

    async def quarters(
        self, agents: Sequence[tuple[str, str]], start_ms: int, end_ms: int
    ) -> tuple[dict[int, int], dict[int, tuple[int, int, int, float | None]]]:
        """The turns started and the tokens used in each quarter hour of the
        time, by the quarter's start."""
        if not agents:
            return {}, {}
        started = Binary(
            operator="/",
            left=Column(name="started_at_ms"),
            right=Value(value=QUARTER_MS),
        )
        used = Binary(
            operator="/", left=Column(name="at_ms"), right=Value(value=QUARTER_MS)
        )
        turns = await self._select(
            Select(
                table="turns",
                columns={"quarter": started, "turns": Call(name="count")},
                where=All(
                    conditions=(of(agents), since("started_at_ms", start_ms, end_ms))
                ),
                group_by=(started,),
            )
        )
        usage = await self._select(
            Select(
                table="usage",
                columns={
                    "quarter": used,
                    **{
                        column: Call(name="sum", arguments=(Column(name=column),))
                        for column in (
                            "input_tokens",
                            "cached_tokens",
                            "output_tokens",
                            "cost_usd",
                        )
                    },
                },
                where=All(conditions=(of(agents), since("at_ms", start_ms, end_ms))),
                group_by=(used,),
            )
        )
        return (
            {
                cast(int, row["quarter"]) * QUARTER_MS: cast(int, row["turns"])
                for row in turns
            },
            {
                cast(int, row["quarter"]) * QUARTER_MS: (
                    cast(int, row["input_tokens"]),
                    cast(int, row["cached_tokens"]),
                    cast(int, row["output_tokens"]),
                    cast(float | None, row["cost_usd"]),
                )
                for row in usage
            },
        )

    async def feed(self, agents: Sequence[tuple[str, str]], limit: int) -> list[Event]:
        """The newest of what the agents did worth a glance: turns begun,
        replies sent, new contacts waiting, the calls of a turn just over
        and reminders gone off, 0.2's by its names."""
        if not agents:
            return []
        rows = await self._select(
            Select(
                table="events",
                joins=(
                    Join(
                        table="turns",
                        kind="left",
                        on=All(
                            conditions=tuple(
                                Compare(
                                    left=Column(name=name, table="turns"),
                                    operator="=",
                                    right=Column(name=name, table="events"),
                                )
                                for name in ("computer_id", "agent_id", "turn_id")
                            )
                        ),
                    ),
                ),
                columns={
                    name: Column(name=name, table="events")
                    for name in (
                        "id",
                        "computer_id",
                        "agent_id",
                        "event_name",
                        "thread_id",
                        "created_at_ms",
                        "turn_id",
                        "message_id",
                        "payload",
                        "envelope",
                    )
                },
                where=All(
                    conditions=(
                        of(agents, "events"),
                        AnyOf(
                            conditions=(
                                In(
                                    expression=Column(
                                        name="event_name", table="events"
                                    ),
                                    values=(*STARTED, *SENT, "reminder.fired"),
                                ),
                                equals("contact", 1, "events"),
                                # a turn over that called tools
                                All(
                                    conditions=(
                                        In(
                                            expression=Column(
                                                name="event_name", table="events"
                                            ),
                                            values=OVER,
                                        ),
                                        AnyOf(
                                            conditions=tuple(
                                                Compare(
                                                    left=Column(
                                                        name=kind, table="turns"
                                                    ),
                                                    operator=">",
                                                    right=Value(value=0),
                                                )
                                                for kind in KINDS
                                            )
                                        ),
                                    )
                                ),
                            )
                        ),
                    )
                ),
                order_by=(
                    Sort(expression=Column(name="id", table="events"), descending=True),
                ),
                limit=limit,
            )
        )
        return [event(row) for row in rows]

    async def departures(
        self, computer_ids: Sequence[str], limit: int
    ) -> list[tuple[str, int]]:
        """The newest times the computers left, and which."""
        if not computer_ids:
            return []
        rows = await self._select(
            Select(
                table="departures",
                where=In(
                    expression=Column(name="computer_id"), values=tuple(computer_ids)
                ),
                order_by=(Sort(expression=Column(name="seen_ms"), descending=True),),
                limit=limit,
            )
        )
        return [
            (cast(str, row["computer_id"]), cast(int, row["seen_ms"])) for row in rows
        ]

    async def replies(
        self, computer_id: str, agent_id: str, message_ids: Sequence[str]
    ) -> list[Event]:
        """The replies given, and every reply and tool call of the turns they
        were sent in, oldest first."""
        agent = (equals("computer_id", computer_id), equals("agent_id", agent_id))
        given = await self._select(
            Select(
                table="events",
                columns={"turn_id": Column(name="turn_id")},
                where=All(
                    conditions=(
                        *agent,
                        In(
                            expression=Column(name="message_id"),
                            values=tuple(message_ids),
                        ),
                        In(expression=Column(name="event_name"), values=SENT),
                        IsNull(expression=Column(name="turn_id"), exclude=True),
                    )
                ),
            )
        )
        turn_ids = tuple(sorted({cast(str, row["turn_id"]) for row in given}))
        if not turn_ids:
            return []
        rows = await self._select(
            Select(
                table="events",
                where=All(
                    conditions=(
                        *agent,
                        In(expression=Column(name="turn_id"), values=turn_ids),
                        In(
                            expression=Column(name="event_name"),
                            values=(
                                *SENT,
                                "harness.tool.started",
                                "harness.tool.completed",
                                "harness.tool.failed",
                            ),
                        ),
                    )
                ),
                order_by=(Sort(expression=Column(name="id")),),
            )
        )
        return [event(row) for row in rows]
