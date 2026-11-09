"""What the computers report, kept once and added up: events reported again
after the server restarts count once and take no turn back to an earlier
step, usage is counted from each runtime session's last totals, and what is
older than the retention, what a computer leaves behind and its departures
go as they should."""

from __future__ import annotations

lazy import asyncio
lazy from pathlib import Path

import aiosqlite
from bazaar_contrib_harness import CommandDetail, ContentDeltaKind
from node import agent, connected, enrol, known, now_ms
from said import beat, called, ended, received, requested, step, turn, used
from served import admin, client, serving

KANA = agent("kana", "有马佳奈")
TABLES = ("events", "turns", "usage", "departures")


async def rows(home: Path, sql: str, *parameters: object) -> list[tuple[object, ...]]:
    async with (
        aiosqlite.connect(home / ".bcn" / "bcs.sqlite3") as connection,
        connection.execute(sql, parameters) as cursor,
    ):
        return [tuple(row) for row in await cursor.fetchall()]


async def settled(home: Path, sql: str, expected: object, wait: float = 30) -> None:
    """Wait until the query gives what is expected: events are kept as the
    server hears them, a moment after it answers the report."""
    found = None
    try:
        async with asyncio.timeout(wait):
            while True:
                found = await rows(home, sql)
                if found == expected:
                    return
                await asyncio.sleep(0.2)
    except TimeoutError:
        raise AssertionError(found) from None


async def test_events_reported_again_after_a_restart_are_kept_once(home: Path):
    async with client() as boss:
        async with serving() as served:
            await admin(served, boss)
            ref, token = await enrol(served, boss, "kana-vm")
            async with connected(served, token, ref, [KANA]) as computer:
                await known(served, boss, ref, "有马佳奈")
                working = turn("kana", "t-dev", "m-1")
                batch = [
                    received(
                        computer,
                        "kana",
                        "t-dev",
                        "m-1",
                        "然后呢",
                        "yuchanns",
                        group="#dev",
                    ),
                    requested(computer, working),
                    used(computer, working, 1000, 4000, 200, 0.5),
                    ended(computer, working),
                    beat(computer),
                ]
                await computer.report(*batch)
                await settled(home, "SELECT count(*) FROM events", [(5,)])
                run = computer.run
        async with (
            serving() as served,
            connected(served, token, ref, [KANA]) as computer,
        ):
            # the same run, its confirmations gone with the server
            computer.run, computer.sequence = run, len(batch)
            await computer.report(*batch)
            await computer.report(beat(computer))
            await settled(
                home,
                "SELECT count(*) FROM events WHERE event_name = 'node.health'",
                [(1,)],
            )
        assert await rows(home, "SELECT count(*) FROM events") == [(5,)]
        assert await rows(
            home,
            "SELECT input_tokens, cached_tokens, output_tokens, cost_usd, outcome"
            " FROM turns",
        ) == [(1000, 4000, 200, 0.5, "completed")]
        assert await rows(home, "SELECT count(*) FROM usage") == [(1,)]


async def test_a_step_reported_again_takes_its_turn_no_step_back(home: Path):
    async with client() as boss:
        async with serving() as served:
            await admin(served, boss)
            ref, token = await enrol(served, boss, "kana-vm")
            async with connected(served, token, ref, [KANA]) as computer:
                await known(served, boss, ref, "有马佳奈")
                working = turn("kana", "t-dev", "m-1")
                at = now_ms()
                batch = [
                    requested(computer, working, at_ms=at),
                    step(
                        computer, working, ContentDeltaKind.AGENT_MESSAGE, at_ms=at + 1
                    ),
                    *called(
                        computer,
                        working,
                        "c-1",
                        CommandDetail(command="ls"),
                        ended=False,
                        at_ms=at + 2,
                    ),
                ]
                await computer.report(*batch)
                await settled(home, "SELECT step FROM turns", [("tool",)])
                run = computer.run
        async with (
            serving() as served,
            connected(served, token, ref, [KANA]) as computer,
        ):
            computer.run, computer.sequence = run, len(batch)
            await computer.report(*batch)
            await computer.report(beat(computer))
            await settled(
                home,
                "SELECT count(*) FROM events WHERE event_name = 'node.health'",
                [(1,)],
            )
        assert await rows(home, "SELECT step FROM turns") == [("tool",)]


async def test_usage_counts_what_each_session_used_since_its_last_totals(home: Path):
    async with client() as boss, serving() as served:
        await admin(served, boss)
        ref, token = await enrol(served, boss, "kana-vm")
        async with connected(served, token, ref, [KANA]) as computer:
            first = turn("kana", "t-dev", "m-1")
            second = turn("kana", "t-dev", "m-2")
            other = turn("kana", "t-dm", "m-3", session="s-2", kind="dm")
            await computer.report(
                requested(computer, first),
                used(computer, first, 1000, 0, 100, 0.25),
                used(computer, first, 1500, 200, 150, 0.375),
                ended(computer, first),
                requested(computer, other),
                used(computer, other, 700, 0, 70, None),
                ended(computer, other),
                # the runtime started counting again, in the same session
                requested(computer, second),
                used(computer, second, 300, 0, 20, 0.0625),
                ended(computer, second),
            )
            await settled(home, "SELECT count(*) FROM usage", [(4,)])
        assert await rows(
            home,
            "SELECT session_id, input_tokens, cached_tokens, output_tokens, cost_usd"
            " FROM usage ORDER BY id",
        ) == [
            ("s-1", 1000, 0, 100, 0.25),
            ("s-1", 500, 200, 50, 0.125),
            ("s-2", 700, 0, 70, None),
            ("s-1", 300, 0, 20, 0.0625),
        ]
        assert await rows(
            home,
            "SELECT turn_id, input_tokens, cached_tokens, output_tokens, cost_usd"
            " FROM turns ORDER BY turn_id",
        ) == [
            ("turn-m-1", 1500, 200, 150, 0.375),
            ("turn-m-2", 300, 0, 20, 0.0625),
            ("turn-m-3", 700, 0, 70, None),
        ]


async def test_what_is_older_than_the_retention_goes_at_start_but_last_totals(
    home: Path,
):
    long_ago = now_ms() - 3 * 86_400_000
    async with client() as boss:
        async with serving(retention_days=2) as served:
            await admin(served, boss)
            ref, token = await enrol(served, boss, "kana-vm")
            async with connected(served, token, ref, [KANA]) as computer:
                old = turn("kana", "t-dev", "m-1")
                new = turn("kana", "t-dev", "m-2")
                await computer.report(
                    requested(computer, old, at_ms=long_ago),
                    used(computer, old, 1000, 0, 100),
                    ended(computer, old, at_ms=long_ago),
                    requested(computer, new),
                    ended(computer, new),
                )
                await settled(home, "SELECT count(*) FROM events", [(5,)])
        # three days pass for what came first
        async with aiosqlite.connect(home / ".bcn" / "bcs.sqlite3") as database:
            await database.execute(
                "UPDATE events SET received_at_ms = ? WHERE turn_id = 'turn-m-1'",
                (long_ago,),
            )
            await database.execute("UPDATE usage SET at_ms = ?", (long_ago,))
            await database.commit()
        async with serving(retention_days=2) as served:
            await settled(home, "SELECT turn_id FROM turns", [("turn-m-2",)])
            # the session's last totals stay, and what it uses next counts from them
            async with connected(served, token, ref, [KANA]) as computer:
                later = turn("kana", "t-dev", "m-3")
                await computer.report(
                    requested(computer, later),
                    used(computer, later, 1100, 0, 120),
                    ended(computer, later),
                )
                await settled(home, "SELECT count(*) FROM usage", [(2,)])
        assert await rows(
            home, "SELECT DISTINCT turn_id FROM events ORDER BY turn_id"
        ) == [("turn-m-2",), ("turn-m-3",)]
        assert await rows(
            home, "SELECT turn_id, input_tokens, output_tokens FROM usage ORDER BY id"
        ) == [("turn-m-1", 1000, 100), ("turn-m-3", 100, 20)]


async def test_a_computer_leaves_once_and_takes_what_it_reported_when_removed(
    home: Path,
):
    async with client() as boss, serving() as served:
        await admin(served, boss)
        ref, token = await enrol(served, boss, "kana-vm")
        kept_ref, kept_token = await enrol(served, boss, "aqua-mbp")
        # the same agent on both, working in a group of each
        for each, key, thread, group in (
            (ref, token, "t-dev", "#dev"),
            (kept_ref, kept_token, "t-ops", "#ops"),
        ):
            async with connected(served, key, each, [KANA]) as computer:
                await known(served, boss, each, "有马佳奈")
                working = turn("kana", thread, "m-1")
                await computer.report(
                    received(
                        computer, "kana", thread, "m-1", "开工", "yuchanns", group=group
                    ),
                    requested(computer, working),
                    used(computer, working, 1000, 0, 100),
                    ended(computer, working),
                )
        # away for as long as a computer counts as online: said twice, kept once
        await settled(home, "SELECT count(*) FROM departures", [(2,)], wait=120)
        feed = (await boss.get(f"{served.url}/ui/activity/feed")).text
        assert all(name in feed for name in ("kana-vm", "aqua-mbp", "#dev", "#ops"))
        gone = token.partition(":")[0]
        removed = await boss.post(f"{served.url}/ui/computers/{ref}/remove")
        assert removed.status_code == 204
        for table in TABLES:
            await settled(
                home,
                f"SELECT count(*) FROM {table} WHERE computer_id = '{gone}'",
                [(0,)],
            )
            assert await rows(
                home,
                f"SELECT count(*) > 0 FROM {table} WHERE computer_id = ?",
                kept_token.partition(":")[0],
            ) == [(1,)]
