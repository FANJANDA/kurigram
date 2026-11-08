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

from __future__ import annotations as _annotations

import pytest

from pyrogram import enums, filters
from pyrogram.types import Chat, Message, MessageEntity

# No filter under test reads the client argument.
CLIENT = None

PRIVATE = Chat(id=42, type=enums.ChatType.PRIVATE)

BOLD = MessageEntity(type=enums.MessageEntityType.BOLD, offset=0, length=4)


@pytest.mark.asyncio
async def test_a_text_with_entities_passes() -> None:
    message = Message(id=1, chat=PRIVATE, text="bold", entities=[BOLD])

    assert await filters.entities(CLIENT, message)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        pytest.param(Message(id=1, chat=PRIVATE, text="plain"), id="plain-text"),
        pytest.param(Message(id=1, chat=PRIVATE, text="plain", entities=[]), id="empty-list"),
        pytest.param(
            Message(id=1, chat=PRIVATE, caption="bold", caption_entities=[BOLD]),
            id="caption-entities",
        ),
    ],
)
async def test_a_message_without_text_entities_does_not(message: Message) -> None:
    assert not await filters.entities(CLIENT, message)


@pytest.mark.asyncio
async def test_it_combines_with_the_other_filters() -> None:
    message = Message(id=1, chat=PRIVATE, text="bold", entities=[BOLD])

    assert await (filters.text & filters.entities)(CLIENT, message)
    assert not await (filters.entities & ~filters.text)(CLIENT, message)
