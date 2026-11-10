"""The activity in a real browser, wide and on a phone: an agent of a 0.2
computer that came back, its turn in progress with every kind of call,
today's turns and usage, the whole record with 0.2's lines in it, and the
agents in its stall and the market's feed on the overview."""

from __future__ import annotations

lazy import asyncio
lazy import re
lazy from pathlib import Path
lazy from typing import Any

import aiosqlite
import httpx
import pytest
from bazaar_contrib_harness import (
    AgentDetail,
    ChangeDetail,
    ChangedFile,
    CommandAction,
    CommandDetail,
    ContentDeltaKind,
    FileDetail,
    McpDetail,
    WebDetail,
)
from bazaar_contrib_server_access import hash_secret
from node import Node, agent, connected, known, now_ms
from playwright.async_api import async_playwright, expect
from said import (
    called,
    ended,
    model,
    received,
    reminded,
    replied,
    requested,
    step,
    turn,
    used,
)
from served import Served, admin, client, serving
from test_schema import restore

MINUTE = 60_000
KANA = agent(
    "agent-1",
    "有马佳奈",
    (("telegram", "@armslave_bot"),),
    (("claudecode", "claude-opus-5-5"), ("codex", "gpt-5.5-codex")),
)


async def workshop(served: Served, session: httpx.AsyncClient) -> str:
    """The ref of 0.2's computer, as the computers' list links it."""
    table = (await session.get(f"{served.url}/ui/computers/table")).text
    found = re.search(r'hx-get="/ui/computers/(\d+)"[^>]*>\s*<div class="pc">', table)
    assert found, table[:3000]
    return found.group(1)


def happened(node: Node, now: int) -> list[dict[str, Any]]:
    """Yesterday a turn that read a page and more of files, today two turns
    and a reminder done, one failed, and one in progress that called every
    kind of tool and is writing its reply."""
    yesterday = now - 86_400_000 + 3 * MINUTE
    old = turn("agent-1", "thread-1", "m-0")
    events = [
        received(
            node,
            "agent-1",
            "thread-1",
            "m-0",
            "整理一下笔记",
            "yuchanns",
            group="#dev",
            at_ms=yesterday,
        ),
        requested(node, old, at_ms=yesterday),
    ]
    for n in range(60):
        events += called(
            node,
            old,
            f"c-{n}",
            FileDetail(action="read", path=f"notes/{n:02}.md"),
            duration_ms=40,
            at_ms=yesterday + n * 1000,
        )
    events.append(ended(node, old, at_ms=yesterday + 70_000))
    for index, text in enumerate(
        ("帮我确认发布状态", "README 太口语化", "看看 opendal")
    ):
        at = now - (40 - index * 10) * MINUTE
        done = turn("agent-1", "thread-1", f"m-{index + 1}", session=f"s-{index}")
        events += [
            received(
                node,
                "agent-1",
                "thread-1",
                f"m-{index + 1}",
                text,
                "yuchanns",
                group="#dev",
                at_ms=at,
            ),
            requested(node, done, at_ms=at),
            model(node, done, "claude-opus-5-5"),
            *called(
                node,
                done,
                f"d-{index}",
                CommandDetail(command="gh pr checks 145", exit_code=0, output="ok"),
                duration_ms=900,
                at_ms=at + 2000,
            ),
            used(node, done, 2000, 6000, 300, 0.25),
        ]
        if index == 1:
            events.append(ended(node, done, failure="上下文超出限制", at_ms=at + 9000))
        else:
            events += [
                replied(node, done, f"o-{index}", "CI 全部通过了", to=f"m-{index + 1}"),
                ended(node, done, at_ms=at + 9000),
            ]
    due = turn("agent-1", "thread-1", "m-r", session="s-r")
    events += [
        reminded(
            node, "agent-1", "thread-1", "m-r", "跟进 PR #145", at_ms=now - 8 * MINUTE
        ),
        requested(node, due, at_ms=now - 8 * MINUTE),
        ended(node, due, at_ms=now - 7 * MINUTE),
    ]
    live = turn("agent-1", "thread-1", "m-live", session="s-live")
    events += [
        received(
            node,
            "agent-1",
            "thread-1",
            "m-live",
            "然后接下来是啥",
            "yuchanns",
            group="#dev",
            at_ms=now - 3 * MINUTE,
        ),
        requested(node, live, at_ms=now - 3 * MINUTE),
        *called(
            node,
            live,
            "l-1",
            CommandDetail(
                command="/bin/bash -lc 'git log --oneline -3'",
                cwd="/home/kana/bcn",
                actions=(CommandAction(kind="other", command="git log --oneline -3"),),
            ),
            duration_ms=1200,
        ),
        *called(
            node,
            live,
            "l-2",
            FileDetail(action="read", path="AGENTS.md"),
            duration_ms=5,
        ),
        *called(
            node,
            live,
            "l-3",
            ChangeDetail(changes=(ChangedFile(path="README.md", kind="update"),)),
            duration_ms=8,
        ),
        *called(
            node,
            live,
            "l-4",
            WebDetail(action="open", url="https://opendal.apache.org"),
        ),
        *called(
            node,
            live,
            "l-5",
            McpDetail(
                server="notes", tool="search_notes", arguments={"query": "第 11 阶段"}
            ),
            duration_ms=400,
        ),
        *called(
            node, live, "l-6", AgentDetail(description="核对发布说明"), duration_ms=8000
        ),
        used(node, live, 11_000, 27_000, 2_100, 0.5),
        step(node, live, ContentDeltaKind.AGENT_MESSAGE),
    ]
    return events


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["wide", "phone"])
async def test_the_activity_reads_as_the_agent_worked(
    home: Path, size: tuple[int, int]
):
    await restore(home)
    # 0.2's computer comes back with a token of its own
    async with aiosqlite.connect(home / ".bcn" / "bcs.sqlite3") as database:
        await database.execute(
            "UPDATE computers SET secret_hash = ? WHERE id = 'computer'",
            (await asyncio.to_thread(hash_secret, "workshop-secret"),),
        )
        await database.commit()
    async with client() as boss, serving() as served, async_playwright() as driver:
        await admin(served, boss)
        ref = await workshop(served, boss)
        now = now_ms()
        async with connected(
            served,
            "computer:workshop-secret",
            ref,
            [KANA],
            {"agent-1": now - 3 * MINUTE},
        ) as node:
            short = await known(served, boss, ref, "有马佳奈")
            events = happened(node, now)
            for start in range(0, len(events), 100):
                await node.report(*events[start : start + 100])
            browser = await driver.chromium.launch()
            try:
                context = await browser.new_context(
                    locale="zh-CN", viewport={"width": size[0], "height": size[1]}
                )
                await context.add_cookies(
                    [
                        {"name": c.name, "value": c.value or "", "url": served.url}
                        for c in boss.cookies.jar
                    ]
                )
                page = await context.new_page()
                await page.goto(f"{served.url}/agents/{short}")
                shown = page.frame_locator("iframe.frame")

                # the turn in progress: each call as it went, then the reply being written
                live = shown.frame_locator("iframe[title='live']")
                await expect(live.locator(".live-turn")).to_have_count(1)
                await expect(live.locator(".step")).to_have_count(7)
                await expect(live.locator(".step.now")).to_have_count(1)
                await expect(live.locator(".quote")).to_contain_text("然后接下来是啥")
                # today's turns, the newest three on a phone, and today's hours
                today = shown.frame_locator("iframe[title='today']")
                await expect(today.locator(".turn:visible")).to_have_count(
                    3 if size[0] < 960 else 5
                )
                await expect(today.locator(".turn .bad")).to_have_count(1)
                usage = shown.frame_locator("iframe[title='usage']")
                await expect(usage.locator(".bars i")).to_have_count(24)

                # the whole record, opened from today's turns, a day at a time
                await today.locator("a.more").click()
                await expect(page).to_have_url(re.compile(r"tab=log"))
                log = shown.frame_locator("iframe.pane")
                await expect(log.locator(".ev")).to_have_count(50)
                await expect(log.locator(".lday")).to_have_count(2)
                # the rest of the record, yesterday's day going on under its heading
                await log.locator(".log-ft button").click()
                await expect(log.locator(".log-ft")).to_have_count(0)
                assert await log.locator(".ev").count() > 50
                await expect(log.locator(".lday")).to_have_count(2)
                # what is reported now comes in at the top
                await node.report(
                    received(
                        node,
                        "agent-1",
                        "thread-1",
                        "m-new",
                        "新的一条",
                        "yuchanns",
                        group="#dev",
                    )
                )
                await expect(log.locator(".ev").first).to_contain_text("新的一条")
                # 0.2's lines among the rest, failures by themselves
                await (
                    log.locator(".log-hd .seg a" if size[0] >= 960 else ".chips a")
                    .filter(has_text="渠道")
                    .click()
                )
                await expect(log.locator(".ev", has_text="帮我看看 CI")).to_have_count(
                    1
                )
                await (
                    log.locator(".log-hd .seg a" if size[0] >= 960 else ".chips a")
                    .filter(has_text="失败")
                    .click()
                )
                await expect(log.locator(".ev.bad")).to_have_count(2)
                await expect(log.locator(".ev", has_text="TimeoutError")).to_have_count(
                    1
                )
                # opened at a turn: its lines marked first, the latest a click away
                await page.goto(f"{served.url}/ui/activity/log/{short}?turn=turn-m-2")
                await expect(page.locator(".ev").first).to_have_class(
                    re.compile(r"\bat\b")
                )
                await expect(page.locator(".ev.at")).not_to_have_count(0)
                await page.locator(".log-at a").click()
                await expect(page.locator(".ev").first).to_contain_text("新的一条")
                await expect(page.locator(".ev.at")).to_have_count(0)

                # the agents in the computer's stall, and the market's feed
                await page.goto(f"{served.url}/")
                board = page.frame_locator("iframe.frame")
                goods = board.frame_locator(
                    "iframe[src='/ui/computers/stall']"
                ).frame_locator("iframe.goods")
                await expect(goods.locator(".vendor.focus")).to_contain_text("有马佳奈")
                feed = board.frame_locator("iframe[src='/ui/activity/feed']")
                await expect(feed.locator(".feed-item").first).to_be_visible()
                await expect(
                    feed.locator(".feed-item", has_text="有马佳奈")
                ).not_to_have_count(0)
            finally:
                await browser.close()
