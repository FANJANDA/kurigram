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

"""`send_media_group()` hands its `progress` callback to the upload of every local file."""

from __future__ import annotations as _annotations

import logging
from typing import TYPE_CHECKING, BinaryIO, Final

import pytest

from pyrogram import Client, raw, types
from pyrogram.methods.messages.send_media_group import SendMediaGroup
from pyrogram.parser import Parser

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pyrogram._typing import PathType

_UPLOADED_FILE: Final[raw.types.InputFile] = raw.types.InputFile(
    id=1234567890,
    parts=1,
    name="upload.bin",
    md5_checksum="",
)


async def report(current: int, total: int, *args: object) -> None:
    pass


class FakeClient(SendMediaGroup):
    """A client that records every `save_file()` call and sends the album nowhere."""

    mimetypes = Client.mimetypes
    guess_mime_type = Client.guess_mime_type

    def __init__(self) -> None:
        self.parser = Parser(None)
        self.uploads: list[tuple[object, Callable | None, tuple]] = []
        self.sent: raw.functions.messages.SendMultiMedia | None = None

    def rnd_id(self) -> int:
        return 1

    async def resolve_peer(self, peer_id: int | str) -> raw.base.InputPeer:
        return raw.types.InputPeerUser(user_id=int(peer_id), access_hash=0)

    async def save_file(
        self,
        path: PathType | BinaryIO | None,
        file_id: int | None = None,
        file_part: int = 0,
        progress: Callable | None = None,
        progress_args: tuple = (),
    ) -> raw.types.InputFile | None:
        if path is None:
            return None

        self.uploads.append((path, progress, progress_args))

        return _UPLOADED_FILE

    async def invoke(
        self,
        query: raw.functions.messages.UploadMedia | raw.functions.messages.SendMultiMedia,
        sleep_threshold: float | None = None,
        business_connection_id: str | None = None,
    ) -> raw.base.MessageMedia | raw.base.messages.Messages:
        if isinstance(query, raw.functions.messages.UploadMedia):
            return raw.types.MessageMediaDocument(
                document=raw.types.Document(
                    id=len(self.uploads),
                    access_hash=0,
                    file_reference=b"",
                    date=0,
                    mime_type="application/zip",
                    size=1,
                    dc_id=2,
                    attributes=[],
                ),
            )

        self.sent = query

        return raw.types.messages.Messages(messages=[], chats=[], users=[], topics=[])


@pytest.fixture
def album(tmp_path: Path) -> list[str]:
    paths = []

    for name in ("first.zip", "second.zip"):
        path = tmp_path / name
        path.write_bytes(b"x")
        paths.append(str(path))

    return paths


@pytest.mark.asyncio
async def test_every_file_of_the_album_reports_its_upload_progress(album: list[str]) -> None:
    client = FakeClient()

    await client.send_media_group(
        chat_id=7,
        media=[types.InputMediaDocument(path) for path in album],
        progress=report,
        progress_args=("album",),
    )

    assert client.uploads == [(path, report, ("album",)) for path in album]
    assert client.sent is not None
    assert len(client.sent.multi_media) == len(album)


@pytest.mark.asyncio
async def test_an_album_sent_without_progress_uploads_without_one(album: list[str]) -> None:
    client = FakeClient()

    await client.send_media_group(
        chat_id=7,
        media=[types.InputMediaDocument(path) for path in album],
    )

    assert client.uploads == [(path, None, ()) for path in album]


@pytest.mark.asyncio
async def test_an_argument_of_an_older_signature_is_logged_and_the_album_still_sent(
    album: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeClient()

    with caplog.at_level(logging.WARNING, logger="pyrogram.methods.messages.send_media_group"):
        await client.send_media_group(
            chat_id=7,
            media=[types.InputMediaDocument(path) for path in album],
            reply_to_message_id=3,
        )

    assert client.sent is not None
    assert "reply_to_message_id" in caplog.text
