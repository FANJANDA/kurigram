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

"""`get_chat_messages()` reads a range of ids one `get_messages()` call at a time."""

from __future__ import annotations as _annotations

from typing import TYPE_CHECKING, cast

import pytest

from pyrogram import enums, types
from pyrogram.methods.messages.get_chat_history import GetChatHistory

if TYPE_CHECKING:
    import pyrogram

CHAT = types.Chat(id=-1001, type=enums.ChatType.CHANNEL)

# Ids with no message: deleted ones come back from `get_messages()` as an `empty` message.
DELETED = frozenset({3})


class FakeClient(GetChatHistory):
    """A client whose chat holds messages 1 to 5, with message 3 deleted."""

    def __init__(self) -> None:
        self.requested: list[int] = []

    async def get_messages(self, chat_id: int | str, message_ids: int) -> types.Message | None:
        self.requested.append(message_ids)

        if message_ids in DELETED:
            return types.Message(id=message_ids, empty=True)

        return types.Message(id=message_ids, chat=CHAT, text=str(message_ids))


def client() -> pyrogram.Client:
    # `FakeClient` subclasses only the mixin, and the method pins `self: pyrogram.Client`.
    return cast("pyrogram.Client", FakeClient())


@pytest.mark.asyncio
async def test_every_id_of_the_range_is_read_oldest_first() -> None:
    app = client()

    messages = [m async for m in app.get_chat_messages(CHAT.id, start_id=1, end_id=5)]

    assert [m.id for m in messages] == [1, 2, 3, 4, 5]
    assert cast("FakeClient", app).requested == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_a_deleted_id_is_yielded_as_an_empty_message() -> None:
    messages = [m async for m in client().get_chat_messages(CHAT.id, start_id=2, end_id=4)]

    assert [m.empty for m in messages] == [None, True, None]


@pytest.mark.asyncio
async def test_reverse_false_reads_newest_first() -> None:
    messages = [
        m async for m in client().get_chat_messages(CHAT.id, start_id=1, end_id=3, reverse=False)
    ]

    assert [m.id for m in messages] == [3, 2, 1]


@pytest.mark.asyncio
async def test_an_id_the_server_answers_nothing_for_is_skipped() -> None:
    class Silent(FakeClient):
        async def get_messages(self, chat_id: int | str, message_ids: int) -> types.Message | None:
            return None if message_ids == 2 else await super().get_messages(chat_id, message_ids)

    app = cast("pyrogram.Client", Silent())

    messages = [m async for m in app.get_chat_messages(CHAT.id, start_id=1, end_id=3)]

    assert [m.id for m in messages] == [1, 3]
