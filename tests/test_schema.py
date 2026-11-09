"""The events taken over: a fresh database gets them with the turns, usage
and departures made of them; 0.2's, with its events, keeps every row as it
was without the foreign key, and has its turns and usage made from them,
once."""

from __future__ import annotations

lazy import asyncio
lazy from pathlib import Path

import aiosqlite
import pytest
from node import now_ms
from served import serving

PACKAGE = "bazaar-contrib-server-activity"
# 0.2's columns, as its rows were
COLUMNS = (
    "id, computer_id, run_id, seq, agent_id, event_name, thread_id,"
    " created_at_ms, received_at_ms, payload"
)


async def query(home: Path, sql: str, *parameters: object) -> list[tuple[object, ...]]:
    async with (
        aiosqlite.connect(home / ".bcn" / "bcs.sqlite3") as connection,
        connection.execute(sql, parameters) as cursor,
    ):
        return [tuple(row) for row in await cursor.fetchall()]


async def restore(home: Path) -> None:
    """0.2's database with what its node reported a day ago."""
    fixtures = (await asyncio.to_thread(Path(__file__).resolve)).parent / "fixtures"
    dump = await asyncio.to_thread((fixtures / "server-v7-events.sql").read_text)
    path = home / ".bcn" / "bcs.sqlite3"
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as connection:
        await connection.executescript(dump)
        shift = now_ms() - 86_400_000 - 1792214700000
        await connection.execute(
            "UPDATE events SET created_at_ms = created_at_ms + ?,"
            " received_at_ms = received_at_ms + ?",
            (shift, shift),
        )
        await connection.commit()


async def test_a_fresh_database_gets_the_events_and_what_is_made_of_them(home: Path):
    async with serving():
        pass
    tables = {
        row[0]
        for row in await query(
            home, "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"events", "turns", "usage", "departures"} <= tables
    assert await query(home, "SELECT * FROM pragma_foreign_key_list('events')") == []
    assert [
        row[0]
        for row in await query(
            home,
            "SELECT version FROM contrib_schema_migrations WHERE package = ?"
            " ORDER BY version",
            PACKAGE,
        )
    ] == [1, 2]


async def test_the_events_of_0_2_are_taken_over_once_with_their_turns_and_usage(
    home: Path,
):
    await restore(home)
    before = await query(home, f"SELECT {COLUMNS} FROM events ORDER BY id")
    assert await query(home, "SELECT * FROM pragma_foreign_key_list('events')") != []
    async with serving():
        pass
    assert await query(home, "SELECT * FROM pragma_foreign_key_list('events')") == []
    assert await query(home, f"SELECT {COLUMNS} FROM events ORDER BY id") == before
    # the session, turn and message each belongs to, from its correlation
    assert await query(
        home,
        "SELECT session_id, turn_id, message_id, envelope FROM events"
        " WHERE event_name IN ('channel.inbound.persisted', 'usage.updated')"
        " ORDER BY id",
    ) == [
        (None, None, "41", None),
        ("rs-1", "turn-1", "41", None),
        ("rs-1", "turn-1", "41", None),
        (None, None, "42", None),
        (None, None, "43", None),
    ]
    # the message that opened a conversation still waiting is a new contact
    assert await query(home, "SELECT id FROM events WHERE contact = 1") == [(13,)]
    started = before[1][7]
    assert await query(
        home,
        "SELECT turn_id, session_id, thread_id, channel, target_kind, message_id,"
        " started_at_ms - ?, ended_at_ms - ?, outcome, reason, input_tokens,"
        " cached_tokens, output_tokens, cost_usd, tools FROM turns ORDER BY turn_id",
        started,
        started,
    ) == [
        (
            "turn-1",
            "rs-1",
            "thread-1",
            "telegram",
            "group",
            "41",
            0,
            10_000,
            "completed",
            None,
            2100,
            5000,
            500,
            0.5,
            1,
        ),
        (
            "turn-2",
            "rs-1",
            "thread-1",
            "telegram",
            "group",
            "42",
            60_000,
            89_000,
            "failed",
            "runtime turn failed: TimeoutError",
            0,
            0,
            0,
            None,
            0,
        ),
    ]
    assert await query(
        home,
        "SELECT session_id, turn_id, input_tokens, cached_tokens, output_tokens,"
        " cost_usd, total_input, total_cached, total_output, total_cost"
        " FROM usage ORDER BY id",
    ) == [
        ("rs-1", "turn-1", 1300, 3000, 300, 0.25, 1300, 3000, 300, 0.25),
        ("rs-1", "turn-1", 800, 2000, 200, 0.25, 2100, 5000, 500, 0.5),
    ]
    taken = await query(
        home,
        "SELECT version, name FROM contrib_schema_migrations WHERE package = ?",
        PACKAGE,
    )
    turns = await query(home, "SELECT * FROM turns ORDER BY turn_id")
    async with serving():
        pass
    assert (
        await query(
            home,
            "SELECT version, name FROM contrib_schema_migrations WHERE package = ?",
            PACKAGE,
        )
        == taken
    )
    assert await query(home, "SELECT * FROM turns ORDER BY turn_id") == turns
    assert await query(home, f"SELECT {COLUMNS} FROM events ORDER BY id") == before


@pytest.mark.parametrize(
    "deleted",
    [
        "DELETE FROM events WHERE id = (SELECT max(id) FROM events)",
        "DELETE FROM events",
    ],
    ids=["newest", "all"],
)
async def test_the_events_go_on_above_the_highest_id_0_2_ever_gave(
    home: Path, deleted: str
):
    await restore(home)
    ((highest,),) = await query(home, "SELECT max(id) FROM events")
    async with aiosqlite.connect(home / ".bcn" / "bcs.sqlite3") as connection:
        await connection.execute(deleted)
        await connection.commit()
    async with serving():
        pass
    async with aiosqlite.connect(home / ".bcn" / "bcs.sqlite3") as connection:
        cursor = await connection.execute(
            "INSERT INTO events (computer_id, run_id, seq, event_name, created_at_ms,"
            " received_at_ms, payload) VALUES ('computer', 'run', 1, 'node.health',"
            " 0, 0, '{}')"
        )
        await connection.commit()
    assert cursor.lastrowid is not None and cursor.lastrowid > int(str(highest))
