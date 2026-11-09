"""What the computers report, kept once: each event written as it comes and,
the first time only, the turn and usage it adds to; the streamed text and
the runtimes' own protocol lines are not kept, they only move their turn's
step. Departures, a computer's removal and the retention clear the rest."""

from __future__ import annotations

from dataclasses import dataclass
lazy import json
lazy from time import time_ns
lazy from typing import cast

from pydantic import TypeAdapter
lazy from bazaar_contrib_audit import AuditEvent, AuditFailure
lazy from bazaar_contrib_harness import (
    AgentDetail,
    ChangeDetail,
    CommandDetail,
    ContentDelta,
    FileDetail,
    HarnessOutput,
    McpDetail,
    ModelUpdated,
    OtherDetail,
    ToolCall,
    ToolCallCompleted,
    ToolCallDetail,
    ToolCallFailed,
    ToolCallStarted,
    UsageUpdated,
    WebDetail,
)
lazy from bazaar_contrib_storage import (
    All,
    AnyOf,
    Binary,
    Call,
    Column,
    Compare,
    Conflict,
    Database,
    Delete,
    Expression,
    Insert,
    IsNull,
    Predicate,
    Scalar,
    Select,
    Session,
    Sort,
    Subquery,
    Update,
    Value,
)

# the text a turn streams and the runtimes' own protocol lines: not kept
STREAMED = frozenset({"harness.content.delta", "harness.tool.text.delta"})
# what the turn is doing, by the kind of text it streams
STEPS = {
    "agent_message": "reply",
    "plan": "thinking",
    "reasoning_text": "thinking",
    "reasoning_summary": "thinking",
}
# the events that end a turn, and how
ENDS = {
    "harness.completed": "completed",
    "harness.failed": "failed",
    "harness.cancelled": "cancelled",
}

HARNESS = TypeAdapter(HarnessOutput)


def equals(name: str, value: Scalar, table: str | None = None) -> Compare:
    return Compare(
        left=Column(name=name, table=table), operator="=", right=Value(value=value)
    )


def kind(detail: ToolCallDetail) -> str:
    """The column a tool call counts in, by the words of its receipt."""
    match detail:
        case CommandDetail():
            return "commands"
        case FileDetail():
            return "files"
        case ChangeDetail():
            return "changes"
        case WebDetail(action="open"):
            return "pages"
        case WebDetail():
            return "searches"
        case McpDetail() | OtherDetail():
            return "tools"
        case AgentDetail():
            return "tasks"


def contact(name: str, metadata: object) -> int:
    """1 for a message that opened a conversation still waiting to be let
    in, a new contact as the feed shows it; 0 for any other event."""
    return int(
        name == "channel.inbound.persisted"
        and isinstance(metadata, dict)
        and metadata.get("channel_session_mapping") == "created"
        and metadata.get("review") == "pending"
    )


def fresh(
    computer_id: str,
    agent_id: str,
    turn_id: str,
    *,
    started_ms: int,
    session_id: str | None = None,
    thread_id: str | None = None,
    channel: str | None = None,
    target_kind: str | None = None,
    connection: str | None = None,
    message_id: str | None = None,
) -> dict[str, Scalar]:
    """A turn just begun: nothing used, nothing called, not over."""
    return {
        "computer_id": computer_id,
        "agent_id": agent_id,
        "turn_id": turn_id,
        "session_id": session_id,
        "thread_id": thread_id,
        "channel": channel,
        "target_kind": target_kind,
        "connection": connection,
        "model": None,
        "message_id": message_id,
        "started_at_ms": started_ms,
        "ended_at_ms": None,
        "outcome": None,
        "reason": None,
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "cost_usd": None,
        "commands": 0,
        "files": 0,
        "changes": 0,
        "pages": 0,
        "searches": 0,
        "tools": 0,
        "tasks": 0,
        "step": None,
        "step_at_ms": None,
    }


@dataclass(frozen=True, slots=True)
class Tally:
    """A runtime session's tokens - fresh input, cache hits and output - and
    their cost, when its runtime gives one."""

    input: int
    cached: int
    output: int
    cost: float | None

    def since(self, before: Tally | None) -> Tally:
        """What was used since the totals before: a count lower than it was
        means the runtime counted again from zero, so all of it is new, as
        is everything with nothing before."""
        if before is None:
            return self
        return Tally(
            self.input - before.input if self.input >= before.input else self.input,
            self.cached - before.cached
            if self.cached >= before.cached
            else self.cached,
            self.output - before.output
            if self.output >= before.output
            else self.output,
            None
            if self.cost is None
            else self.cost - before.cost
            if before.cost is not None and self.cost >= before.cost
            else self.cost,
        )

    def row(self) -> dict[str, Scalar]:
        return {
            "input_tokens": self.input,
            "cached_tokens": self.cached,
            "output_tokens": self.output,
            "cost_usd": self.cost,
        }

    def totals(self) -> dict[str, Scalar]:
        return {
            "total_input": self.input,
            "total_cached": self.cached,
            "total_output": self.output,
            "total_cost": self.cost,
        }


class Ledger:
    def __init__(self, database: Database) -> None:
        self._database = database
        # the step each running turn was last put at here, so that the text it
        # streams writes only when the step changes
        self._steps: dict[tuple[str, str, str], str] = {}

    async def record(self, computer_id: str, event: AuditEvent) -> None:
        """Keep the event, once however often it comes, and add it to its
        turn, usage or computer; streamed text only moves its turn's step."""
        name = event.event_name
        context = event.context
        turn = All(
            conditions=(
                equals("computer_id", computer_id),
                equals("agent_id", context.agent_id),
                equals("turn_id", context.turn_id),
            )
        )
        if name in STREAMED or name.startswith("harness.source."):
            if name != "harness.content.delta" or context.turn_id is None:
                return
            delta = cast(ContentDelta, HARNESS.validate_python(event.payload).payload)
            await self._step(computer_id, event, turn, STEPS[delta.kind])
            return
        payload = event.payload
        # a payload starlink left out for its size: only the name, envelope and context are there
        omitted = isinstance(payload, dict) and payload.keys() == {"omitted"}
        output = (
            HARNESS.validate_python(payload)
            if not omitted
            and name
            in {
                "harness.tool.completed",
                "harness.tool.failed",
                "harness.usage.updated",
                "harness.model.updated",
            }
            else None
        )
        row = {
            "computer_id": computer_id,
            "run_id": event.run_id,
            "seq": event.sequence,
            "agent_id": context.agent_id or None,
            "event_name": name,
            "thread_id": context.thread_id,
            "created_at_ms": event.occurred_at_ms,
            "received_at_ms": time_ns() // 1_000_000,
            "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "envelope": event.model_dump_json(
                include={"id", "level", "error", "context", "observed_at_ms"},
                exclude_none=True,
            ),
            "session_id": context.session_id,
            "turn_id": context.turn_id,
            "message_id": context.message_id,
            "contact": contact(
                name, payload.get("metadata") if isinstance(payload, dict) else None
            ),
        }

        async def write(session: Session) -> None:
            inserted = await session.insert(
                Insert(
                    table="events",
                    rows=(row,),
                    conflict=Conflict(columns=("computer_id", "run_id", "seq")),
                    returning=("id",),
                )
            )
            if not inserted.count:
                return
            if name in ENDS or name == "harness.turn.requested":
                # its step begins again, or it has none
                self._steps.pop(
                    (computer_id, context.agent_id, context.turn_id or ""), None
                )
            if name == "harness.turn.requested":
                await session.insert(
                    Insert(
                        table="turns",
                        rows=(
                            fresh(
                                computer_id,
                                context.agent_id,
                                cast(str, context.turn_id),
                                started_ms=event.occurred_at_ms,
                                session_id=context.session_id,
                                thread_id=context.thread_id,
                                channel=context.channel,
                                target_kind=context.target_kind,
                                connection=context.connection,
                                message_id=context.message_id,
                            ),
                        ),
                        # tried again on the next runtime: running again, on it
                        conflict=Conflict(
                            columns=("computer_id", "agent_id", "turn_id"),
                            update=(
                                "session_id",
                                "connection",
                                "ended_at_ms",
                                "outcome",
                                "reason",
                                "step",
                                "step_at_ms",
                            ),
                        ),
                    )
                )
            elif name in ENDS:
                await session.insert(
                    Insert(
                        table="turns",
                        rows=(
                            fresh(
                                computer_id,
                                context.agent_id,
                                cast(str, context.turn_id),
                                started_ms=event.occurred_at_ms,
                                session_id=context.session_id,
                                thread_id=context.thread_id,
                                channel=context.channel,
                                target_kind=context.target_kind,
                                connection=context.connection,
                                message_id=context.message_id,
                            )
                            | {
                                "ended_at_ms": event.occurred_at_ms,
                                "outcome": ENDS[name],
                                "reason": event.error.message
                                if isinstance(event, AuditFailure)
                                else None,
                            },
                        ),
                        # one that failed to start ends without having begun
                        conflict=Conflict(
                            columns=("computer_id", "agent_id", "turn_id"),
                            update=(
                                "ended_at_ms",
                                "outcome",
                                "reason",
                                "step",
                                "step_at_ms",
                            ),
                        ),
                    )
                )
            elif name == "harness.tool.started":
                await self._moved(session, computer_id, event, turn, "tool")
            elif name in ("harness.tool.completed", "harness.tool.failed"):
                call = (
                    None
                    if output is None
                    else cast(ToolCallCompleted | ToolCallFailed, output.payload).call
                )
                if call is None:
                    call = await self._started(session, computer_id, event)
                if call is not None:
                    counted = kind(call.detail)
                    await session.update(
                        Update(
                            table="turns",
                            values={
                                counted: Binary(
                                    operator="+",
                                    left=Column(name=counted),
                                    right=Value(value=1),
                                )
                            },
                            where=turn,
                        )
                    )
            elif name == "harness.model.updated" and output is not None:
                await session.update(
                    Update(
                        table="turns",
                        values={
                            "model": Value(
                                value=cast(ModelUpdated, output.payload).model
                            )
                        },
                        where=turn,
                    )
                )
            elif name == "harness.usage.updated" and output is not None:
                await self._used(session, computer_id, event, output, turn)
            elif name == "node.health":
                # only the newest beat of each computer says anything, as in 0.2
                await session.delete(
                    Delete(
                        table="events",
                        where=All(
                            conditions=(
                                equals("computer_id", computer_id),
                                equals("event_name", name),
                                Compare(
                                    left=Column(name="id"),
                                    operator="<",
                                    right=Value(value=inserted.rows[0]["id"]),
                                ),
                            )
                        ),
                    )
                )

        await self._database.transaction(write)

    async def _step(
        self, computer_id: str, event: AuditEvent, turn: Predicate, step: str
    ) -> None:
        """The turn is doing something else now, if it is."""
        key = (computer_id, event.context.agent_id, event.context.turn_id or "")
        if self._steps.get(key) == step:
            return

        async def write(session: Session) -> None:
            await self._moved(session, computer_id, event, turn, step)

        await self._database.transaction(write)

    async def _moved(
        self,
        session: Session,
        computer_id: str,
        event: AuditEvent,
        turn: Predicate,
        step: str,
    ) -> None:
        """The turn's step, unless it was put at a later one: an event reported
        again comes after those that followed it."""
        moved = await session.update(
            Update(
                table="turns",
                values={
                    "step": Value(value=step),
                    "step_at_ms": Value(value=event.occurred_at_ms),
                },
                where=All(
                    conditions=(
                        turn,
                        IsNull(expression=Column(name="ended_at_ms")),
                        AnyOf(
                            conditions=(
                                IsNull(expression=Column(name="step_at_ms")),
                                Compare(
                                    left=Column(name="step_at_ms"),
                                    operator="<=",
                                    right=Value(value=event.occurred_at_ms),
                                ),
                            )
                        ),
                    )
                ),
            )
        )
        if moved.count:
            self._steps[
                computer_id, event.context.agent_id, event.context.turn_id or ""
            ] = step

    async def _started(
        self, session: Session, computer_id: str, event: AuditEvent
    ) -> ToolCall | None:
        """The call as it started, for one whose end came without its
        payload."""
        rows = await session.select(
            Select(
                table="events",
                columns={"payload": Column(name="payload")},
                where=All(
                    conditions=(
                        equals("computer_id", computer_id),
                        equals("agent_id", event.context.agent_id),
                        equals("turn_id", event.context.turn_id),
                        equals("event_name", "harness.tool.started"),
                    )
                ),
                order_by=(Sort(expression=Column(name="id"), descending=True),),
            )
        )
        for row in rows:
            payload = json.loads(cast(str, row["payload"]))
            if payload.keys() == {"omitted"}:
                continue
            started = cast(ToolCallStarted, HARNESS.validate_python(payload).payload)
            if started.call.call_id == event.context.call_id:
                return started.call
        return None

    async def _used(
        self,
        session: Session,
        computer_id: str,
        event: AuditEvent,
        output: HarnessOutput,
        turn: Predicate,
    ) -> None:
        """What the runtime session used since its row before, written down
        and added to the turn."""
        usage = cast(UsageUpdated, output.payload)
        previous = await session.select(
            Select(
                table="usage",
                where=All(
                    conditions=(
                        equals("computer_id", computer_id),
                        equals("agent_id", event.context.agent_id),
                        equals("session_id", output.session_id),
                    )
                ),
                order_by=(Sort(expression=Column(name="id"), descending=True),),
                limit=1,
            )
        )
        now = Tally(
            (usage.total.input_tokens or 0)
            + (usage.total.cache_write_input_tokens or 0),
            usage.total.cached_input_tokens or 0,
            usage.total.output_tokens or 0,
            usage.cost_usd,
        )
        grown = now.since(
            Tally(
                cast(int, previous[0]["total_input"]),
                cast(int, previous[0]["total_cached"]),
                cast(int, previous[0]["total_output"]),
                cast(float | None, previous[0]["total_cost"]),
            )
            if previous
            else None
        )
        await session.insert(
            Insert(
                table="usage",
                rows=(
                    {
                        "computer_id": computer_id,
                        "agent_id": event.context.agent_id,
                        "session_id": output.session_id,
                        "turn_id": event.context.turn_id,
                        "at_ms": event.occurred_at_ms,
                        **grown.row(),
                        **now.totals(),
                    },
                ),
            )
        )
        added: dict[str, Expression] = {
            column: Binary(
                operator="+", left=Column(name=column), right=Value(value=by)
            )
            for column, by in (
                ("input_tokens", grown.input),
                ("cached_tokens", grown.cached),
                ("output_tokens", grown.output),
            )
        }
        if grown.cost is not None:
            added["cost_usd"] = Binary(
                operator="+",
                left=Call(
                    name="coalesce",
                    arguments=(Column(name="cost_usd"), Value(value=0.0)),
                ),
                right=Value(value=grown.cost),
            )
        await session.update(Update(table="turns", values=added, where=turn))

    async def depart(self, computer_id: str, seen_ms: int) -> None:
        """The computer left after it was last here; each replica may say so."""
        await self._database.insert(
            Insert(
                table="departures",
                rows=({"computer_id": computer_id, "seen_ms": seen_ms},),
                conflict=Conflict(columns=("computer_id", "seen_ms")),
            )
        )

    async def remove(self, computer_id: str) -> None:
        """Everything the computer reported and made, gone with it."""

        async def remove(session: Session) -> None:
            for table in ("events", "turns", "usage", "departures"):
                await session.delete(
                    Delete(table=table, where=equals("computer_id", computer_id))
                )

        await self._database.transaction(remove)

    async def sweep(self, days: int) -> None:
        """What is older than the retention goes: the events received, the
        turns started, the usage and the departures before it, but for each
        runtime session's newest usage, which what it uses next is counted
        from however long it waits."""
        cutoff = time_ns() // 1_000_000 - days * 86_400_000

        def before(column: str) -> Compare:
            return Compare(
                left=Column(name=column), operator="<", right=Value(value=cutoff)
            )

        newest = Select(
            table="usage",
            alias="later",
            columns={
                "id": Call(name="max", arguments=(Column(name="id", table="later"),))
            },
            where=All(
                conditions=tuple(
                    Compare(
                        left=Column(name=name, table="later"),
                        operator="=",
                        right=Column(name=name, table="usage"),
                    )
                    for name in ("computer_id", "agent_id", "session_id")
                )
            ),
        )

        async def sweep(session: Session) -> None:
            for table, where in (
                ("events", before("received_at_ms")),
                ("turns", before("started_at_ms")),
                (
                    "usage",
                    All(
                        conditions=(
                            before("at_ms"),
                            Compare(
                                left=Column(name="id"),
                                operator="<",
                                right=Subquery(query=newest),
                            ),
                        )
                    ),
                ),
                ("departures", before("seen_ms")),
            ):
                await session.delete(Delete(table=table, where=where))

        await self._database.transaction(sweep)
