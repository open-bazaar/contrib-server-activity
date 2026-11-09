"""What an agent's node says as it works, by the contracts of the modules
saying it: a message received or sent and a reminder going off in the
shape the message modules publish, a turn and what its runtime reports as
the agent publishes them, a channel connecting and the pool giving up on a
runtime."""

from __future__ import annotations

lazy from typing import Any

from bazaar_contrib_audit import AuditContext, AuditError
from bazaar_contrib_harness import (
    ContentDelta,
    ContentDeltaKind,
    HarnessFailure,
    HarnessFailureReason,
    HarnessOutput,
    HarnessTurn,
    ModelUpdated,
    TokenUsage,
    ToolCall,
    ToolCallCompleted,
    ToolCallDetail,
    ToolCallFailed,
    ToolCallStarted,
    TurnCompleted,
    TurnFailed,
    UsageUpdated,
)
from node import Node, now_ms


def recorded(
    name: str,
    context: AuditContext,
    metadata: dict[str, Any],
    at_ms: int,
) -> dict[str, Any]:
    """A payload as collaboration, messages-node and reminder publish theirs."""
    return {
        "event_name": name,
        "state": "completed",
        "created_at_ms": at_ms,
        "correlation": {
            "node_id": context.agent_id,
            "channel": context.channel,
            "thread_id": context.thread_id,
        },
        "metadata": metadata,
    }


def received(
    node: Node,
    agent_id: str,
    thread: str,
    message: str,
    text: str,
    sender: str,
    *,
    group: str | None = None,
    first: bool = False,
    pending: bool = False,
    at_ms: int | None = None,
) -> dict[str, Any]:
    at = now_ms() if at_ms is None else at_ms
    context = AuditContext(
        agent_id=agent_id, channel="telegram", thread_id=thread, message_id=message
    )
    return node.event(
        "channel.inbound.persisted",
        context,
        recorded(
            "channel.inbound.persisted",
            context,
            {
                "notifies_runtime": True,
                "review": "pending" if pending else "approved",
                "channel_session_mapping": "created" if first else "reused",
                "thread_mapping": "created" if first else "reused",
                "text": text,
                "target": group or sender,
                "target_kind": "group" if group else "dm",
                "target_name": group,
                "message_type": "text",
                "sender": {
                    "id": sender,
                    "name": sender,
                    "display_name": sender,
                    "kind": "human",
                },
                "reply_to_message_id": None,
                "attachments": [],
            },
            at,
        ),
        at_ms=at,
    )


def replied(
    node: Node,
    turn: AuditContext,
    message: str,
    text: str,
    *,
    to: str | None = None,
    at_ms: int | None = None,
) -> dict[str, Any]:
    at = now_ms() if at_ms is None else at_ms
    context = AuditContext(
        agent_id=turn.agent_id,
        channel=turn.channel,
        thread_id=turn.thread_id,
        message_id=message,
        session_id=turn.session_id,
        turn_id=turn.turn_id,
    )
    return node.event(
        "channel.outbound.sent",
        context,
        recorded(
            "channel.outbound.sent",
            context,
            {"text": text, "reply_to_message_id": to, "attachments": []},
            at,
        ),
        at_ms=at,
    )


def reminded(
    node: Node,
    agent_id: str,
    thread: str,
    message: str,
    title: str,
    *,
    at_ms: int | None = None,
) -> dict[str, Any]:
    at = now_ms() if at_ms is None else at_ms
    context = AuditContext(
        agent_id=agent_id, channel="telegram", thread_id=thread, message_id=message
    )
    return node.event(
        "reminder.fired",
        context,
        recorded(
            "reminder.fired",
            context,
            {"reminder_id": "r-1", "title": title, "target_kind": "group"},
            at,
        ),
        at_ms=at,
    )


def turn(
    agent_id: str,
    thread: str,
    message: str,
    *,
    session: str = "s-1",
    connection: str = "claudecode:0",
    kind: str = "group",
) -> AuditContext:
    """The context of a turn a message set off, as the agent gives it."""
    return AuditContext(
        agent_id=agent_id,
        channel="telegram",
        connection=connection,
        harness=connection.partition(":")[0],
        target_kind=kind,
        thread_id=thread,
        message_id=message,
        sender_id="yuchanns",
        session_id=session,
        turn_id=f"turn-{message}",
    )


def requested(
    node: Node, context: AuditContext, *, at_ms: int | None = None
) -> dict[str, Any]:
    return node.event(
        "harness.turn.requested",
        context,
        HarnessTurn(
            id=str(context.turn_id),
            session_id=str(context.session_id),
            input_text="…",
        ).model_dump(mode="json"),
        at_ms=at_ms,
    )


def output(context: AuditContext, payload: Any) -> HarnessOutput:
    return HarnessOutput(
        session_id=str(context.session_id),
        turn_id=str(context.turn_id),
        payload=payload,
    )


def model(node: Node, context: AuditContext, name: str) -> dict[str, Any]:
    return node.harness(context, output(context, ModelUpdated(model=name)))


def step(
    node: Node,
    context: AuditContext,
    kind: ContentDeltaKind,
    *,
    at_ms: int | None = None,
) -> dict[str, Any]:
    return node.harness(
        context, output(context, ContentDelta(kind=kind, text="…")), at_ms=at_ms
    )


def called(
    node: Node,
    context: AuditContext,
    call_id: str,
    detail: ToolCallDetail,
    *,
    duration_ms: int | None = None,
    failed: str | None = None,
    ended: bool = True,
    at_ms: int | None = None,
) -> list[dict[str, Any]]:
    """A tool call starting and, unless still running, ending."""
    on = context.model_copy(update={"call_id": call_id})
    call = ToolCall(call_id=call_id, name="tool", detail=detail)
    whole = call.model_copy(update={"duration_ms": duration_ms})
    events = [node.harness(on, output(on, ToolCallStarted(call=call)), at_ms=at_ms)]
    if ended:
        events.append(
            node.harness(
                on,
                output(
                    on,
                    ToolCallCompleted(call=whole)
                    if failed is None
                    else ToolCallFailed(call=whole, error_message=failed),
                ),
                at_ms=at_ms,
            )
        )
    return events


def used(
    node: Node,
    context: AuditContext,
    fresh: int,
    cached: int,
    output_tokens: int,
    cost: float | None = None,
) -> dict[str, Any]:
    """The runtime session's running totals."""
    return node.harness(
        context,
        output(
            context,
            UsageUpdated(
                total=TokenUsage(
                    input_tokens=fresh,
                    cached_input_tokens=cached,
                    output_tokens=output_tokens,
                ),
                cost_usd=cost,
            ),
        ),
    )


def ended(
    node: Node,
    context: AuditContext,
    *,
    failure: str | None = None,
    at_ms: int | None = None,
) -> dict[str, Any]:
    return node.harness(
        context,
        output(
            context,
            TurnCompleted(event_name="turn/completed")
            if failure is None
            else TurnFailed(
                event_name="turn/failed",
                failure=HarnessFailure(HarnessFailureReason.EXECUTION, failure),
            ),
        ),
        at_ms=at_ms,
    )


def connected(
    node: Node, agent_id: str, bot: str, *, failed: str | None = None
) -> dict[str, Any]:
    """A channel connecting, or failing to, to try again in five seconds."""
    context = AuditContext(
        agent_id=agent_id, channel="telegram", connection="telegram:0"
    )
    if failed is None:
        return node.event(
            "channel.connection.connected",
            context,
            {"identity": {"id": "1", "name": bot}},
        )
    return node.event(
        "channel.connection.failed",
        context,
        {"retry_in_ms": 5000},
        error=AuditError(kind="channel_connection", message=failed),
    )


def banned(node: Node, context: AuditContext, fallback: str | None) -> dict[str, Any]:
    return node.event(
        "harness.pool.banned",
        context,
        {
            "ban_until_ms": now_ms() + 3_600_000,
            "terminal_state": "failed",
            "fallback": fallback,
        },
    )


def beat(node: Node) -> dict[str, Any]:
    return node.event(
        "node.health",
        AuditContext(agent_id=""),
        {"contribs": [], "version": "0.3.0", "interval_ms": 60_000},
    )
