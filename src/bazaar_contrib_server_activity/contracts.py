"""What other modules hold server-activity to: the turns, receipts, counts
and cursors of the agents an account may see, as the events the computers
report add up to."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
lazy from collections.abc import Mapping, Sequence

from bazaar_core import ServiceKey
lazy from bazaar_contrib_server import Account


@dataclass(frozen=True, slots=True)
class Calls:
    """Tool calls by the kind a receipt words them in: commands run, files
    looked at, files changed, pages opened, web searches, other tools and
    subtasks sent off."""

    commands: int = 0
    files: int = 0
    changes: int = 0
    pages: int = 0
    searches: int = 0
    tools: int = 0
    tasks: int = 0

    @property
    def total(self) -> int:
        return (
            self.commands
            + self.files
            + self.changes
            + self.pages
            + self.searches
            + self.tools
            + self.tasks
        )


@dataclass(frozen=True, slots=True)
class Turn:
    """A turn as its events add up to: where it came from, the runtime and
    model it ran on, how it ended and why, the tokens it used - fresh
    input, cache hits and output - with their cost when the runtime gives
    one, its tool calls, and the step it is at while it runs."""

    computer_id: str
    agent_id: str
    turn_id: str
    session_id: str | None
    thread_id: str | None
    channel: str | None
    target_kind: str | None
    connection: str | None
    model: str | None
    message_id: str | None
    started_ms: int
    ended_ms: int | None
    outcome: Literal["completed", "failed", "cancelled"] | None
    reason: str | None
    input: int
    cached: int
    output: int
    cost: float | None
    calls: Calls
    step: Literal["reply", "thinking", "tool"] | None
    step_ms: int | None


@dataclass(frozen=True, slots=True)
class Receipt:
    """What a turn did before one of its replies, since the reply before
    it or its start: its tool calls and how many seconds that took."""

    calls: Calls
    seconds: int


@dataclass(frozen=True, slots=True)
class Bucket:
    """The turns that started and the tokens used from a moment until the
    next bucket's."""

    start_ms: int
    turns: int
    input: int
    cached: int
    output: int
    cost: float | None


class Activity(Protocol):
    """The activity of the agents an account may see; any other agent is
    not there."""

    async def cursor(
        self,
        account: Account,
        *,
        agent_id: str | None = None,
        thread_id: str | None = None,
        messages: bool = False,
    ) -> int:
        """The id of the newest event of the agent, of every agent the
        account may see for None, in the conversation when one is given;
        only of the messages sent, received and let in when asked: 0 for
        none."""
        ...

    async def working(
        self,
        account: Account,
        *,
        agent_id: str | None = None,
        thread_id: str | None = None,
    ) -> Sequence[Turn]:
        """The turns in progress on computers that are here, oldest first."""
        ...

    async def turns(
        self,
        account: Account,
        since_ms: int,
        until_ms: int,
        *,
        agent_id: str | None = None,
        thread_id: str | None = None,
    ) -> Sequence[Turn]:
        """The turns started in the time, newest first."""
        ...

    async def receipts(
        self, account: Account, agent_id: str, message_ids: Sequence[str]
    ) -> Mapping[str, Receipt]:
        """What the turn behind each reply of the agent did before it, by
        the reply's message id; a reply sent outside a turn has none."""
        ...

    async def stats(
        self,
        account: Account,
        starts: Sequence[int],
        until_ms: int,
        *,
        agent_id: str | None = None,
    ) -> Sequence[Bucket]:
        """The turns and tokens of each bucket, an hour or a day as the
        caller cut them on its clock, from each start until the next and
        the last until the end."""
        ...


ACTIVITY = ServiceKey[Activity]("server.activity")
