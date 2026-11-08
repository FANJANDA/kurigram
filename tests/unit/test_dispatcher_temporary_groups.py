#  Pyrogram - Telegram MTProto API Client Library for Python
#  Copyright (C) 2017-present Dan <https://github.com/delivrance>
#
#  This file is part of Pyrogram.
#
#  Pyrogram is free software: you can redistribute it and/or modify
#  it under the terms of the GNU Lesser General Public License as published
#  by the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  Pyrogram is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with Pyrogram.  If not, see <http://www.gnu.org/licenses/>.

"""Named groups, `remove_all_handler()` and the countdown that drops a temporary group.

A group is temporary unless it is one of `Dispatcher.special_groups` or a name containing one of
`Dispatcher.EXEMPT_GROUP_KEYWORDS`: `handler_timeout` seconds after a handler was last added to
it, the whole group is removed. The timeouts here are shortened to tens of milliseconds, and every
check waits several times longer than the timer it is about, so a slow runner only makes a test
slower, never wrong.
"""

from __future__ import annotations as _annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Final

import pytest

from pyrogram import Client, raw
from pyrogram.handlers import ErrorHandler, MessageHandler, RawUpdateHandler

if TYPE_CHECKING:
    from pyrogram.dispatcher import Dispatcher, HandlerGroup
    from pyrogram.types import Message

_TIMEOUT: Final[float] = 0.05

# Long enough for a timer of `_TIMEOUT` to have fired several times over.
_EXPIRED: Final[float] = _TIMEOUT * 4

# A group that is not exempt, the way an application registers one per user awaiting input.
_TEMPORARY: Final[int] = -12345


async def greet(client: Client, message: Message, /) -> None:
    pass


async def on_error(client: Client, *args: object) -> None:
    pass


@pytest.fixture
def client() -> Client:
    client = Client(name="dispatcher_groups_probe", in_memory=True, no_updates=True)
    client.dispatcher.handler_timeout = _TIMEOUT

    return client


def test_named_groups_are_dispatched_after_every_numbered_one(client: Client) -> None:
    for group in ("listen-10", 3, "kw-9", -5, "edit-2", 0):
        client.add_handler(MessageHandler(greet), group=group)

    # A plain `sorted()` of these keys raises `TypeError: '<' not supported between str and int`.
    assert list(client.dispatcher.groups) == [-5, 0, 3, "kw-9", "edit-2", "listen-10"]


async def test_a_named_group_is_reached_by_a_dispatch(client: Client) -> None:
    fired: list[HandlerGroup] = []

    def recording(group: HandlerGroup) -> RawUpdateHandler:
        async def record(client: Client, *args: object) -> None:
            fired.append(group)

        return RawUpdateHandler(record)

    client.add_handler(recording("listen-1"), group="listen-1")
    client.add_handler(recording(1), group=1)

    dispatcher = client.dispatcher
    dispatcher.updates_queue.put_nowait((raw.types.UpdateDcOptions(dc_options=[]), {}, {}))
    dispatcher.updates_queue.put_nowait(None)

    await dispatcher.handler_worker()

    assert fired == [1, "listen-1"]


def test_remove_all_handler_takes_every_handler_of_the_group(client: Client) -> None:
    client.add_handler(MessageHandler(greet), group="listen-1")
    client.add_handler(MessageHandler(greet), group="listen-1")
    client.add_handler(MessageHandler(greet), group=1)

    client.dispatcher.remove_all_handler("listen-1")

    assert list(client.dispatcher.groups) == [1]


def test_remove_all_handler_of_a_missing_group_is_not_an_error(client: Client) -> None:
    client.dispatcher.remove_all_handler("never-added")

    assert client.dispatcher.groups == {}


@pytest.mark.parametrize(
    "group",
    [0, 1, 10, "listen-1", "edit-1", "kw-1", "invite-1", "comment-listen-1"],
)
def test_an_exempt_group_is_recognised(client: Client, group: HandlerGroup) -> None:
    assert client.dispatcher.is_excluded_group(group)


@pytest.mark.parametrize("group", [_TEMPORARY, -100, 2, "comment-1"])
def test_any_other_group_is_temporary(client: Client, group: HandlerGroup) -> None:
    assert not client.dispatcher.is_excluded_group(group)


async def test_a_temporary_group_is_removed_once_its_countdown_runs_out(client: Client) -> None:
    client.add_handler(MessageHandler(greet), group=_TEMPORARY)

    assert _TEMPORARY in client.dispatcher.groups

    await asyncio.sleep(_EXPIRED)

    assert _TEMPORARY not in client.dispatcher.groups


@pytest.mark.parametrize("group", [0, 1, 10, "listen-1", "edit-1", "kw-1", "invite-1"])
async def test_an_exempt_group_outlives_the_countdown(client: Client, group: HandlerGroup) -> None:
    client.add_handler(MessageHandler(greet), group=group)

    await asyncio.sleep(_EXPIRED)

    assert group in client.dispatcher.groups


async def test_adding_to_a_temporary_group_starts_its_countdown_over(client: Client) -> None:
    client.dispatcher.handler_timeout = _TIMEOUT * 3
    client.add_handler(MessageHandler(greet), group=_TEMPORARY)

    await asyncio.sleep(_TIMEOUT * 2)
    client.add_handler(MessageHandler(greet), group=_TEMPORARY)
    await asyncio.sleep(_TIMEOUT * 2)

    # Four `_TIMEOUT`s after the first handler, past its countdown of three.
    assert len(client.dispatcher.groups[_TEMPORARY]) == 2

    await asyncio.sleep(_TIMEOUT * 3)

    assert _TEMPORARY not in client.dispatcher.groups


async def test_an_error_handler_neither_starts_nor_extends_a_countdown(client: Client) -> None:
    client.add_handler(ErrorHandler(on_error), group=-1)

    await asyncio.sleep(_EXPIRED)

    assert -1 in client.dispatcher.groups

    client.dispatcher.handler_timeout = _TIMEOUT * 3
    client.add_handler(MessageHandler(greet), group=_TEMPORARY)
    await asyncio.sleep(_TIMEOUT * 2)
    client.add_handler(ErrorHandler(on_error), group=_TEMPORARY)
    await asyncio.sleep(_TIMEOUT * 2)

    assert _TEMPORARY not in client.dispatcher.groups


async def test_the_timer_of_a_removed_group_spares_the_group_registered_again(
    client: Client,
) -> None:
    """The old countdown must not take the new handlers down with it when it fires."""
    client.dispatcher.handler_timeout = _TIMEOUT * 3
    client.add_handler(MessageHandler(greet), group=_TEMPORARY)

    await asyncio.sleep(_TIMEOUT * 2)
    client.dispatcher.remove_all_handler(_TEMPORARY)
    again = MessageHandler(greet)
    client.add_handler(again, group=_TEMPORARY)
    await asyncio.sleep(_TIMEOUT * 2)

    assert client.dispatcher.groups[_TEMPORARY] == [again]


async def test_removing_the_last_handler_stops_the_countdown(client: Client) -> None:
    handler = MessageHandler(greet)
    client.add_handler(handler, group=_TEMPORARY)

    client.remove_handler(handler, group=_TEMPORARY)

    assert _TEMPORARY not in client.dispatcher._auto_remove_tokens
    assert _TEMPORARY not in client.dispatcher._auto_remove_timers


def test_a_group_registered_before_any_loop_counts_down_once_started(client: Client) -> None:
    client.add_handler(MessageHandler(greet), group=_TEMPORARY)

    async def run() -> bool:
        await client.dispatcher.start()

        try:
            await asyncio.sleep(_EXPIRED)
            return _TEMPORARY in client.dispatcher.groups
        finally:
            await client.dispatcher.stop()

    assert asyncio.run(run()) is False


async def test_a_group_registered_from_another_thread_counts_down_on_the_client_loop(
    client: Client,
) -> None:
    # What `Client.start()` records.
    client._loop = asyncio.get_running_loop()

    thread = threading.Thread(
        target=client.add_handler,
        args=(MessageHandler(greet),),
        kwargs={"group": _TEMPORARY},
    )
    thread.start()
    thread.join()

    await asyncio.sleep(_EXPIRED)

    assert _TEMPORARY not in client.dispatcher.groups


async def test_a_restart_keeping_handlers_gives_a_temporary_group_its_full_timeout_again(
    client: Client,
) -> None:
    client.dispatcher.handler_timeout = _TIMEOUT * 3
    client.add_handler(MessageHandler(greet), group=_TEMPORARY)

    await client.dispatcher.start()
    await asyncio.sleep(_TIMEOUT * 2)
    await client.dispatcher.stop(clear_handlers=False)
    await client.dispatcher.start()
    await asyncio.sleep(_TIMEOUT * 2)

    assert _TEMPORARY in client.dispatcher.groups

    await asyncio.sleep(_TIMEOUT * 3)
    await client.dispatcher.stop()

    assert _TEMPORARY not in client.dispatcher.groups


async def test_stop_clearing_handlers_cancels_every_countdown() -> None:
    # With updates on, since only then does `stop()` clear the handlers.
    client = Client(name="dispatcher_groups_probe", in_memory=True)
    client.add_handler(MessageHandler(greet), group=_TEMPORARY)

    await client.dispatcher.start()
    await client.dispatcher.stop()

    assert client.dispatcher.groups == {}
    assert client.dispatcher._auto_remove_tokens == {}
    assert client.dispatcher._auto_remove_timers == {}


async def test_stop_keeping_handlers_keeps_the_group_but_not_its_timer(client: Client) -> None:
    client.add_handler(MessageHandler(greet), group=_TEMPORARY)

    await client.dispatcher.start()
    await client.dispatcher.stop(clear_handlers=False)

    assert _TEMPORARY in client.dispatcher._auto_remove_tokens
    assert client.dispatcher._auto_remove_timers == {}


def test_a_client_can_name_its_own_permanent_groups() -> None:
    class Gated(Client):
        dispatcher_special_groups = frozenset({0, 1, -100})
        handler_timeout = 5

    dispatcher: Dispatcher = Gated(name="gated", in_memory=True, no_updates=True).dispatcher

    assert dispatcher.is_excluded_group(-100)
    assert not dispatcher.is_excluded_group(10)
    assert dispatcher.handler_timeout == 5
