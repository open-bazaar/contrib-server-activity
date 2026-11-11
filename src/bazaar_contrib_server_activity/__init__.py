"""The activity of the Bazaar Compute server: what the computers report,
kept once and cleared after the retention, the turns, usage and departures
it adds up to, ACTIVITY for the modules that read them, and the activity's
pages - the sections and the whole record of an agent, the agents in each
stall and the market's feed."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy from collections.abc import AsyncIterator
lazy from importlib.resources import files
lazy from pathlib import Path

from bazaar_contrib_server import SERVER, Topic
from bazaar_contrib_server_access import ACCESS
from bazaar_contrib_server_agent import (
    AGENTS,
    AGENTS_SAID,
    AgentSection,
    AgentsSaid,
    AgentTab,
)
from bazaar_contrib_server_computer import (
    COMPUTER_LEFT,
    COMPUTER_REMOVED,
    COMPUTERS,
    REPORTED,
    ComputerLeft,
    ComputerRemoved,
    Reported,
)
from bazaar_contrib_server_database import SERVER_DB, SERVER_SCHEMA
from bazaar_contrib_server_migrations import SERVER_MIGRATIONS
from bazaar_contrib_server_overview import OVERVIEW, OverviewItem
from bazaar_contrib_timer import TIMER
from bazaar_core import Context, Contrib, ServiceKey
from pydantic import BaseModel, ConfigDict, Field

from .contracts import ACTIVITY, Activity, Bucket, Calls, Receipt, Turn
lazy from .ledger import Ledger
lazy from .pages import VIEW, Views
lazy from .schema import MODULE
lazy from .service import Service
lazy from .store import Store

__all__ = [
    "ACTIVITY",
    "Activity",
    "ActivityConfig",
    "Bucket",
    "Calls",
    "Receipt",
    "Turn",
    "activity",
]

# what the computers report, kept as it comes
_LEDGER = ServiceKey["Ledger"]("server.activity.ledger")
# the retention is swept once a day, as 0.2 did
DAY_MS = 86_400_000


class ActivityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # how many days of what the computers report are kept
    retention_days: int = Field(default=30, ge=1, strict=True)


@asynccontextmanager
async def _activity(ctx: Context, config: ActivityConfig) -> AsyncIterator[None]:
    server = ctx.accept(SERVER)
    agents = ctx.accept(AGENTS)
    timer = ctx.accept(TIMER)
    database = ctx.accept(SERVER_DB)
    resources = Path(str(files(__name__))) / "resources"
    icon = await asyncio.to_thread(
        (resources / "templates" / "activity" / "icons" / "activity.svg").read_text,
        encoding="utf-8",
    )
    async with ctx.accept(SERVER_SCHEMA).register(MODULE):
        ledger = Ledger(database)
        store = Store(database)
        pages = await server.pages("activity", __name__)
        views = Views(
            server,
            pages,
            ctx.accept(ACCESS),
            agents,
            ctx.accept(COMPUTERS),
            store,
        )

        async def sweep() -> None:
            while True:
                await ledger.sweep(config.retention_days)
                await timer.create(DAY_MS).wait()

        async with (
            asyncio.TaskGroup() as tasks,
            server.module(
                "activity",
                ui=pages.application(views.routes),
                static=resources / "static",
                locales=resources / "locales",
            ),
            server.topic(
                Topic(
                    name="activity.agent",
                    fetch=views.followed,
                    fields=("agent",),
                    permission=VIEW,
                )
            ),
            server.topic(Topic(name="activity.feed", fetch=views.fed)),
            server.topic(
                Topic(
                    name="activity.stall",
                    fetch=views.stalled,
                    fields=("computer",),
                    permission=VIEW,
                )
            ),
            agents.section(
                AgentSection(
                    key="live",
                    src="/ui/activity/live",
                    column="main",
                    permission=VIEW,
                    order=10,
                )
            ),
            agents.section(
                AgentSection(
                    key="today",
                    src="/ui/activity/today",
                    column="main",
                    permission=VIEW,
                    order=20,
                )
            ),
            agents.section(
                AgentSection(
                    key="usage",
                    src="/ui/activity/usage",
                    column="side",
                    permission=VIEW,
                    order=20,
                )
            ),
            # the whole record, opened from the activity and lighting it
            agents.tab(
                AgentTab(
                    key="log",
                    text="activity.log.title",
                    icon=icon,
                    src="/ui/activity/log",
                    permission=VIEW,
                    parent="activity",
                )
            ),
            ctx.accept(COMPUTERS).stall("/ui/activity/stall"),
            ctx.accept(OVERVIEW).register(
                OverviewItem(
                    key="feed",
                    title="activity.feed.title",
                    src="/ui/activity/feed",
                    order=40,
                )
            ),
        ):
            task = tasks.create_task(sweep())
            ctx.offer(_LEDGER, ledger)
            ctx.offer(ACTIVITY, Service(store, agents))
            try:
                yield
            finally:
                task.cancel()


async def _reported(ctx: Context, value: Reported) -> None:
    await ctx.accept(_LEDGER).record(value.computer_id, value.event)


async def _removed(ctx: Context, value: ComputerRemoved) -> None:
    await ctx.accept(_LEDGER).remove(value.computer_id)


async def _left(ctx: Context, value: ComputerLeft) -> None:
    await ctx.accept(_LEDGER).depart(value.computer_id, value.seen_ms)


async def _said(ctx: Context, value: AgentsSaid) -> None:
    for agent_id, said in value.agents.items():
        if said.turns is not None and said.said_at_ms is not None:
            await ctx.accept(_LEDGER).ended(
                value.computer_id, agent_id, said.turns, said.said_at_ms
            )


activity = Contrib(
    config=ActivityConfig,
    accepts=[
        SERVER,
        ACCESS,
        OVERVIEW,
        COMPUTERS,
        AGENTS,
        SERVER_DB,
        SERVER_SCHEMA,
        SERVER_MIGRATIONS,
        TIMER,
    ],
    offers=[_LEDGER, ACTIVITY],
    lifespan=_activity,
    listen={
        REPORTED: _reported,
        COMPUTER_REMOVED: _removed,
        COMPUTER_LEFT: _left,
        AGENTS_SAID: _said,
    },
)
