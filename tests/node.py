"""A computer's side of the protocol over real HTTP, with the token its page
gives: it answers what the server asks about its machine and its agents,
and reports its audit events as starlink does, shaped by their contracts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
lazy import asyncio
lazy import re
lazy from collections.abc import AsyncIterator, Sequence
lazy from time import time_ns
lazy from typing import Any
lazy from uuid import uuid7

import httpx
from bazaar_contrib_audit import AuditContext, AuditError, AuditFailure, AuditInfo
from bazaar_contrib_harness import HarnessOutput
from pydantic import JsonValue
from served import Served

GIB = 1024**3
INFO = {
    "version": "0.3.0",
    "system": "Linux",
    "name": "Fedora Linux 44",
    "release": "6.19.10-300.fc44.x86_64",
    "machine": "x86_64",
    "started_ms": time_ns() // 1_000_000 - 3_600_000,
    "cpu": {"cores": 4, "percent": 12.5},
    "memory": {"total": 8 * GIB, "used": 2 * GIB},
    "disks": [{"mount": "/", "total": 40 * GIB, "used": 14 * GIB}],
}


def now_ms() -> int:
    return time_ns() // 1_000_000


def agent(
    agent_id: str,
    name: str,
    channels: Sequence[tuple[str, str]] = (("telegram", "@armslave_bot"),),
    runtimes: Sequence[tuple[str, str]] = (("claudecode", "claude-opus-5-5"),),
) -> dict[str, Any]:
    """An agent as agent-configuration lists it: its channels by bot and
    its runtimes by model."""
    return {
        "id": agent_id,
        "name": name,
        "mode": "session",
        "session": None,
        "idle_timeout": 600,
        "channel": [
            {
                "kind": kind,
                "connection": f"{kind}:{index}",
                "values": {"bot": bot},
                "secrets": [],
            }
            for index, (kind, bot) in enumerate(channels)
        ],
        "harness": [
            {
                "kind": kind,
                "connection": f"{kind}:{index}",
                "values": {"model": model},
                "secrets": [],
            }
            for index, (kind, model) in enumerate(runtimes)
        ],
    }


def activity(
    agents: Sequence[dict[str, Any]], working: dict[str, int]
) -> dict[str, Any]:
    """How agent-activity says the agents are: running, and at work since
    when for those given."""
    return {
        one["id"]: {
            "state": "running",
            "reason": None,
            "usage": {"input": 0, "output": 0, "cached": 0, "cost": None},
            "event": None,
            "event_at_ms": None,
            "started_at_ms": now_ms() - 3_600_000,
            "working": {"since_ms": working[one["id"]], "conversation": None}
            if one["id"] in working
            else None,
            "channels": [
                {
                    "kind": channel["kind"],
                    "connection": channel["connection"],
                    "state": "running",
                    "identity": {"id": "1", "name": channel["values"]["bot"]},
                    "connected_at_ms": now_ms() - 3_600_000,
                    "received_at_ms": None,
                    "failures": 0,
                    "error": None,
                }
                for channel in one["channel"]
            ],
            "runtimes": [],
        }
        for one in agents
    }


@dataclass
class Node:
    """A connected computer: its ref and id, and the run its events come from."""

    ref: str
    id: str
    session: httpx.AsyncClient
    run: str = field(default_factory=lambda: str(uuid7()))
    sequence: int = 0

    def event(
        self,
        name: str,
        context: AuditContext,
        payload: JsonValue,
        *,
        at_ms: int | None = None,
        error: AuditError | None = None,
    ) -> dict[str, Any]:
        """The next audit event of the run, as the node publishes it."""
        self.sequence += 1
        at = now_ms() if at_ms is None else at_ms
        fields = {
            "id": str(uuid7()),
            "run_id": self.run,
            "sequence": self.sequence,
            "event_name": name,
            "occurred_at_ms": at,
            "observed_at_ms": at,
            "monotonic_ns": time_ns(),
            "context": context,
            "payload": payload,
        }
        event = (
            AuditInfo(**fields)
            if error is None
            else AuditFailure(**fields, error=error)
        )
        return event.model_dump(mode="json")

    def harness(
        self, context: AuditContext, output: HarnessOutput, *, at_ms: int | None = None
    ) -> dict[str, Any]:
        """A runtime's event as the agent publishes it, its native line left out."""
        payload = output.payload
        failed = getattr(payload, "failure", None)
        return self.event(
            f"harness.{payload.type}",
            context,
            output.model_dump(mode="json", exclude={"raw"}),
            at_ms=at_ms,
            error=None
            if failed is None and payload.type != "tool.failed"
            else AuditError(
                kind="tool_call_failed" if failed is None else failed.reason.value,
                message=getattr(payload, "error_message", None)
                if failed is None
                else failed.message,
            ),
        )

    async def report(self, *events: dict[str, Any]) -> None:
        sent = await self.session.post(
            "/report", json={"run_id": self.run, "events": list(events)}
        )
        assert sent.status_code == 200, (sent.status_code, sent.text)


async def _answer(
    session: httpx.AsyncClient, agents: list[dict[str, Any]], working: dict[str, int]
) -> None:
    """What the server asks the computer, answered as its node would."""
    after = 0
    while True:
        calls = (await session.get("/listen", params={"after": after})).json()["calls"]
        for call in calls:
            asked = (call["request"]["service"], call["request"]["method"])
            if asked == ("node", "info"):
                answer = {"kind": "result", "value": INFO}
            elif asked == ("agent-configuration", "list"):
                answer = {
                    "kind": "result",
                    "value": {"revision": "1", "agents": agents},
                }
            elif asked == ("agent-activity", "agents"):
                answer = {"kind": "result", "value": activity(agents, working)}
            else:
                answer = {"kind": "error", "type": "LookupError", "message": "absent"}
            await session.put(f"/announce/{call['number']}", json=answer)
            after = call["number"]


async def enrol(
    served: Served, session: httpx.AsyncClient, name: str
) -> tuple[str, str]:
    """A computer the account connects; its ref and its token, as the page
    showing the commands gives them."""
    page = await session.post(f"{served.url}/ui/computers/", data={"name": name})
    assert page.status_code == 200, (page.status_code, page.text[:3000])
    token = re.search(r"--option token=([^\"&\s]+)\"", page.text)
    ref = re.search(r'hx-get="/ui/computers/(\d+)"', page.text)
    assert token is not None and ref is not None
    return ref.group(1), token.group(1)


@asynccontextmanager
async def connected(
    served: Served,
    token: str,
    ref: str,
    agents: list[dict[str, Any]],
    working: dict[str, int] | None = None,
) -> AsyncIterator[Node]:
    """The computer of the token connected, its node answering until it
    stops; the agents given at work since then."""
    async with await asyncio.to_thread(
        lambda: httpx.AsyncClient(
            base_url=f"{served.url}/starlink/v1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=90,
        )
    ) as session:
        listening = asyncio.create_task(_answer(session, agents, working or {}))
        try:
            yield Node(ref, token.partition(":")[0], session)
        finally:
            listening.cancel()
            try:
                await listening
            except asyncio.CancelledError:
                pass


async def known(
    served: Served, session: httpx.AsyncClient, computer: str, name: str
) -> str:
    """The agent's ref once the computer's stall shows it, its node having
    said what its agents are."""
    stall = ""
    try:
        async with asyncio.timeout(45):
            while True:
                stall = (
                    await session.get(f"{served.url}/ui/activity/stall/{computer}")
                ).text
                found = re.search(
                    rf'href="/agents/(\d+)"[^>]*>\s*<span class="lantern[^"]*"></span>\s*<div class="who">\s*<div class="nm"><b>{name}</b>',
                    stall,
                )
                if found:
                    return found.group(1)
                await asyncio.sleep(0.5)
    except TimeoutError:
        raise AssertionError(stall[:3000]) from None
