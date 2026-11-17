"""Keep stderr captured from real provider startup runs through the HTTP report."""

from __future__ import annotations

lazy import asyncio
lazy from pathlib import Path
lazy from typing import Any, cast

import pytest
from bazaar_contrib_audit import AuditEvent
from node import agent, connected, enrol, known
from pydantic import TypeAdapter
from served import admin, client, serving
from test_recording import rows, settled


@pytest.mark.e2e
async def test_native_stderr_is_kept_with_its_session(
    home: Path, pytestconfig: pytest.Config
) -> None:
    journals = pytestconfig.getoption("--native-audit")
    if not journals:
        pytest.skip("Supply --native-audit from a real provider acceptance run")
    adapter = TypeAdapter(AuditEvent)
    async with client() as boss, serving() as served:
        await admin(served, boss)
        for journal in journals:
            text = await asyncio.to_thread(Path(journal).read_text)
            events = [
                event
                for line in text.splitlines()
                for event in (adapter.validate_json(line),)
                if event.event_name == "harness.source.stderr"
            ]
            assert events, journal
            payload = cast(dict[str, Any], events[0].payload)
            provider = payload["provider"]
            identity = agent(events[0].context.agent_id, "Startup acceptance")
            ref, token = await enrol(served, boss, f"stderr-{provider}")
            async with connected(served, token, ref, [identity]) as computer:
                await known(served, boss, ref, "Startup acceptance")
                computer.run = events[0].run_id
                computer.sequence = max(event.sequence for event in events)
                await computer.report(
                    *(event.model_dump(mode="json") for event in events)
                )
                expected = [
                    (
                        event.context.session_id,
                        cast(dict[str, Any], event.payload)["source_id"],
                        cast(dict[str, Any], event.payload)["data"],
                    )
                    for event in events
                ]
                query = (
                    "SELECT session_id, json_extract(payload, '$.source_id'),"
                    " json_extract(payload, '$.data') FROM events"
                    f" WHERE computer_id = '{computer.id}'"
                    " AND event_name = 'harness.source.stderr' ORDER BY seq"
                )
                await settled(home, query, expected)
            assert await rows(home, query) == expected
