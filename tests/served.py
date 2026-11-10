"""The server's entries as installed, server-activity among them, with the
door as a way in for people other than the admin, a module reading
ACTIVITY as other modules do, and a second replica that says once more
that a computer left, as each replica that saw it last may."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
lazy import asyncio
lazy import logging
lazy import re
lazy from collections.abc import AsyncIterator
lazy from dataclasses import asdict
lazy from importlib.resources import files
lazy from pathlib import Path
lazy from typing import Any

import httpx
from bazaar_contrib_http import HTTP
from bazaar_contrib_server import SERVER
from bazaar_contrib_server_access import ACCESS
from bazaar_contrib_server_computer import COMPUTER_LEFT, ComputerLeft
from bazaar_contrib_server_database import SERVER_DB
from bazaar_core import Context, Contrib, ContribEntry, ContribHub, ContribSpec, load
from door import Door
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from bazaar_contrib_server_activity import ACTIVITY

ENTRIES = (
    ("timer-wheel", {}),
    ("http", {"listen": "127.0.0.1:0"}),
    ("server-database", {}),
    ("server-migrations", {}),
    ("server", {}),
    ("server-theme-night", {}),
    ("server-theme-day", {}),
    ("server-access", {}),
    ("server-overview", {}),
    ("server-relay-memory", {}),
    ("server-computer", {}),
    ("server-agent", {}),
)


@dataclass
class Served:
    hub: ContribHub
    url: str
    password: str
    door: Door


@asynccontextmanager
async def serving(retention_days: int = 30) -> AsyncIterator[Served]:
    found: dict[str, Any] = {}
    printed: list[str] = []
    told: set[ComputerLeft] = set()

    class Printed(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            printed.append(record.getMessage())

    @asynccontextmanager
    async def look(ctx: Context) -> AsyncIterator[None]:
        found["url"] = ctx.accept(HTTP).url
        yield

    @asynccontextmanager
    async def enter(ctx: Context) -> AsyncIterator[None]:
        server = ctx.accept(SERVER)
        pages = await server.pages("door", "door")
        locales = (
            (await asyncio.to_thread(lambda: Path(str(files("door")))))
            / "resources"
            / "locales"
        )

        async def settings(request: Request) -> Response:
            return await pages.render(request, "door/settings.html")

        found["door"] = Door(ctx.accept(ACCESS), ctx.accept(SERVER_DB))
        async with (
            server.module(
                "door", ui=pages.application([Route("/", settings)]), locales=locales
            ),
            ctx.accept(ACCESS).method(found["door"]),
        ):
            yield

    @asynccontextmanager
    async def probe(ctx: Context) -> AsyncIterator[None]:
        """What ACTIVITY gives the account asking, as JSON: GET
        /ui/probe/<what> with the agent, the messages, the time and the
        starts of the buckets in the query."""
        activity = ctx.accept(ACTIVITY)

        async def read(request: Request) -> Response:
            account = request.state.account
            query = request.query_params
            agent_id = query.get("agent")
            match request.path_params["what"]:
                case "cursor":
                    value = await activity.cursor(
                        account, agent_id=agent_id, messages="messages" in query
                    )
                case "working":
                    value = [
                        turn.turn_id
                        for turn in await activity.working(account, agent_id=agent_id)
                    ]
                case "turns":
                    value = [
                        [turn.turn_id, turn.outcome, turn.input, turn.calls.total]
                        for turn in await activity.turns(
                            account,
                            int(query["since"]),
                            int(query["until"]),
                            agent_id=agent_id,
                        )
                    ]
                case "receipts":
                    value = {
                        message: [
                            asdict(receipt.calls),
                            receipt.seconds,
                            receipt.turn_id,
                        ]
                        for message, receipt in (
                            await activity.receipts(
                                account, query["agent"], query.getlist("message")
                            )
                        ).items()
                    }
                case _:
                    value = [
                        asdict(bucket)
                        for bucket in await activity.stats(
                            account,
                            [int(start) for start in query.getlist("start")],
                            int(query["until"]),
                            agent_id=agent_id,
                        )
                    ]
            return JSONResponse(value)

        async with ctx.accept(SERVER).module(
            "probe", ui=Starlette(routes=[Route("/{what}", read)])
        ):
            yield

    async def again(ctx: Context, value: ComputerLeft) -> None:
        if value not in told:
            told.add(value)
            await ctx.announce(COMPUTER_LEFT, value)

    composition = await asyncio.to_thread(
        load,
        [
            *(ContribSpec(contrib=name, config=config) for name, config in ENTRIES),
            ContribSpec(
                contrib="server-activity", config={"retention_days": retention_days}
            ),
        ],
    )
    composition = composition.model_copy(
        update={
            "entries": (
                *composition.entries,
                ContribEntry(
                    name="door",
                    definition=Contrib(
                        accepts=[SERVER, ACCESS, SERVER_DB], lifespan=enter
                    ),
                ),
                ContribEntry(
                    name="probe",
                    definition=Contrib(accepts=[SERVER, ACTIVITY], lifespan=probe),
                ),
                ContribEntry(
                    name="look", definition=Contrib(accepts=[HTTP], lifespan=look)
                ),
                ContribEntry(
                    name="replica", definition=Contrib(listen={COMPUTER_LEFT: again})
                ),
            )
        }
    )
    logger = logging.getLogger("bazaar_contrib_server_access")
    handler = Printed()
    logger.addHandler(handler)
    try:
        async with ContribHub() as hub, hub.enable(composition):
            password = next(
                (
                    created.group(1)
                    for message in printed
                    if (created := re.search(r"with password (\S+);", message))
                ),
                "",
            )
            yield Served(hub, found["url"], password, found["door"])
    finally:
        logger.removeHandler(handler)


@asynccontextmanager
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # building the client loads certificates from disk, which is not the loop's work
    async with await asyncio.to_thread(httpx.AsyncClient, timeout=90) as session:
        yield session


async def admin(served: Served, session: httpx.AsyncClient) -> None:
    await session.post(
        f"{served.url}/login/", data={"name": "admin", "password": served.password}
    )


async def role(served: Served, session: httpx.AsyncClient, *points: str) -> str:
    """A new role holding the points, given to whoever first comes through a
    door; its id."""
    made = await session.post(
        f"{served.url}/ui/access/roles/",
        data={"name": "holding-" + "-".join(points), "permissions": list(points)},
    )
    assert made.status_code == 303
    await session.post(f"{served.url}{made.headers['location']}/default")
    return made.headers["location"].rsplit("/", 1)[1]


async def through(served: Served, session: httpx.AsyncClient, person: str) -> None:
    """Log in through the door as the person."""
    sent = await session.get(f"{served.url}/login/door/?person={person}")
    while (
        sent.status_code in (302, 303, 307)
        and "/login/door/" in sent.headers["location"]
    ):
        sent = await session.get(f"{served.url}{sent.headers['location']}")
