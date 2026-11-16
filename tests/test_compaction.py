"""Context compaction in the activity log as the node reports it."""

from __future__ import annotations

import pytest
from bazaar_contrib_harness import ContextCompactionCompleted, ContextCompactionStarted
from node import agent, connected, enrol, known
from playwright.async_api import async_playwright, expect
from said import output, turn
from served import admin, client, serving


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["wide", "phone"])
@pytest.mark.parametrize(
    "language,started,completed,source",
    [
        ("zh-CN", "开始压缩上下文", "上下文压缩完成", "运行时"),
        ("en", "Context compaction started", "Context compaction completed", "Runtime"),
    ],
    ids=["zh", "en"],
)
async def test_compaction_is_visible_and_follows_new_events(
    size: tuple[int, int],
    language: str,
    started: str,
    completed: str,
    source: str,
):
    async with client() as boss, serving() as served, async_playwright() as driver:
        await admin(served, boss)
        ref, token = await enrol(served, boss, "kana-vm")
        async with connected(served, token, ref, [agent("kana", "有马佳奈")]) as node:
            short = await known(served, boss, ref, "有马佳奈")
            browser = await driver.chromium.launch()
            try:
                context = await browser.new_context(
                    locale=language, viewport={"width": size[0], "height": size[1]}
                )
                await context.add_cookies(
                    [
                        {"name": c.name, "value": c.value or "", "url": served.url}
                        for c in boss.cookies.jar
                    ]
                )
                page = await context.new_page()
                log = f"{served.url}/ui/activity/log/{short}"
                await page.goto(log)

                on = turn("kana", "t-dev", "m-compaction")
                await node.report(
                    node.harness(on, output(on, ContextCompactionStarted()))
                )
                await expect(page.locator(".ev", has_text=started)).to_be_visible()
                await node.report(
                    node.harness(on, output(on, ContextCompactionCompleted()))
                )
                await expect(page.locator(".ev", has_text=completed)).to_be_visible()

                await page.goto(f"{log}?filter=runtime")
                for text in (started, completed):
                    line = page.locator(".ev", has_text=text)
                    await expect(line).to_be_visible()
                    await expect(line.locator(".src")).to_have_text(source)
                await page.goto(f"{log}?filter=failed")
                for text in (started, completed):
                    await expect(page.locator(".ev", has_text=text)).to_have_count(0)
            finally:
                await browser.close()
