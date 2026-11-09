"""What ACTIVITY gives other modules, and what the pages show, for the agents
the account asking may see: the admin every one, another person only what
is shared with their role."""

from __future__ import annotations

lazy import asyncio
lazy from typing import Any

import httpx
from bazaar_contrib_harness import CommandDetail, FileDetail, WebDetail
from node import agent, connected, enrol, known, now_ms
from said import called, ended, received, replied, requested, turn, used
from served import Served, admin, client, role, serving, through

KANA = agent("kana", "有马佳奈")
AQUA = agent("aqua", "啊夸")


async def probe(
    served: Served, session: httpx.AsyncClient, what: str, **query: Any
) -> Any:
    answer = await session.get(f"{served.url}/ui/probe/{what}", params=query)
    assert answer.status_code == 200, answer.text[:3000]
    return answer.json()


async def test_activity_gives_what_the_account_may_see():
    async with client() as boss, client() as bob, serving() as served:
        await admin(served, boss)
        viewers = await role(served, boss, "agents.view")
        await through(served, bob, "bob")
        ref, token = await enrol(served, boss, "kana-vm")
        async with connected(served, token, ref, [KANA, AQUA]) as computer:
            kana = await known(served, boss, ref, "有马佳奈")
            aqua = await known(served, boss, ref, "啊夸")
            now = now_ms()
            done = turn("kana", "t-dev", "m-1")
            await computer.report(
                received(
                    computer,
                    "kana",
                    "t-dev",
                    "m-1",
                    "合一下",
                    "yuchanns",
                    group="#dev",
                    at_ms=now - 60_000,
                ),
                requested(computer, done, at_ms=now - 60_000),
                *called(computer, done, "c-1", CommandDetail(command="git fetch")),
                *called(computer, done, "c-2", CommandDetail(command="git merge")),
                *called(computer, done, "c-3", FileDetail(action="read", path="a.md")),
                used(computer, done, 1000, 500, 100),
                replied(computer, done, "o-1", "合好了", at_ms=now - 30_000),
                *called(
                    computer, done, "c-4", WebDetail(action="open", url="https://x")
                ),
                replied(computer, done, "o-2", "顺便看了文档", at_ms=now - 10_000),
                ended(computer, done),
                received(
                    computer,
                    "kana",
                    "t-li",
                    "m-li",
                    "你好",
                    "lilei",
                    first=True,
                    pending=True,
                ),
                requested(computer, turn("kana", "t-dev", "m-2")),
                requested(computer, turn("aqua", "t-ops", "m-3")),
            )
            # kept a moment after the report is answered
            async with asyncio.timeout(30):
                while True:
                    if len(await probe(served, boss, "working")) == 2:
                        break
                    await asyncio.sleep(0.2)

            # what each reply took since the turn began or its reply before
            assert await probe(
                served, boss, "receipts", agent="kana", message=["o-1", "o-2"]
            ) == {
                "o-1": [
                    {
                        "commands": 2,
                        "files": 1,
                        "changes": 0,
                        "pages": 0,
                        "searches": 0,
                        "tools": 0,
                        "tasks": 0,
                    },
                    30,
                ],
                "o-2": [
                    {
                        "commands": 0,
                        "files": 0,
                        "changes": 0,
                        "pages": 1,
                        "searches": 0,
                        "tools": 0,
                        "tasks": 0,
                    },
                    20,
                ],
            }
            assert sorted(await probe(served, boss, "working")) == [
                "turn-m-2",
                "turn-m-3",
            ]
            assert await probe(
                served,
                boss,
                "turns",
                agent="kana",
                since=now - 3_600_000,
                until=now + 60_000,
            ) == [["turn-m-2", None, 0, 0], ["turn-m-1", "completed", 1000, 4]]
            hour = now - now % 3_600_000
            stats = await probe(
                served,
                boss,
                "stats",
                start=[hour - 3_600_000, hour],
                until=hour + 3_600_000,
            )
            assert [bucket["start_ms"] for bucket in stats] == [hour - 3_600_000, hour]
            assert sum(bucket["turns"] for bucket in stats) == 3
            assert sum(bucket["input"] + bucket["cached"] for bucket in stats) == 1500
            messages = await probe(served, boss, "cursor", agent="kana", messages="")
            assert 0 < messages < await probe(served, boss, "cursor", agent="kana")

            # nothing shared yet: another person sees none of it
            assert await probe(served, bob, "working") == []
            assert await probe(served, bob, "cursor") == 0
            assert (
                await bob.get(f"{served.url}/ui/activity/live/{kana}")
            ).status_code == 404
            await boss.post(
                f"{served.url}/ui/access/shares/agent/{kana}",
                data={"roles": [viewers], "grant": [f"{viewers} agents.view"]},
            )
            assert await probe(served, bob, "working") == ["turn-m-2"]
            assert (
                await probe(served, bob, "receipts", agent="aqua", message=["o-1"])
                == {}
            )
            assert (
                await bob.get(f"{served.url}/ui/activity/live/{kana}")
            ).status_code == 200
            assert (
                await bob.get(f"{served.url}/ui/activity/log/{aqua}")
            ).status_code == 404
            feed = (await bob.get(f"{served.url}/ui/activity/feed")).text
            assert "有马佳奈" in feed and "啊夸" not in feed
            # a new contact is on the feed, a message in a conversation let in is not
            assert "lilei" in feed and "yuchanns" not in feed
