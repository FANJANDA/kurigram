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

import asyncio
import functools
import inspect
import io
import logging
import math
import os
from collections import OrderedDict
from hashlib import md5
from pathlib import PurePath
from typing import Union, BinaryIO, Callable

import pyrogram
from pyrogram import raw
from pyrogram.errors import BadRequest, FilePartMissing, FloodPremiumWait, FloodWait

log = logging.getLogger(__name__)

PART_SIZE = 512 * 1024
BIG_FILE_THRESHOLD = 10 * 1024 * 1024
# Attempts per part before the whole upload is failed.
PART_MAX_ATTEMPTS = 5
# Longest flood wait honoured in place; longer waits are raised to the caller.
PART_FLOOD_WAIT_CAP = 60
PART_BACKOFF_CAP = 30.0
# send_* methods re-save a part in a `while True` loop on FILE_PART_X_MISSING.
# After this many re-saves of the same file, raise so the caller re-uploads it.
MISSING_PART_RESAVE_LIMIT = 5

_resave_counts: "OrderedDict[int, int]" = OrderedDict()
_sleep = asyncio.sleep  # Replaceable in tests.


class UploadPartError(RuntimeError):
    """Telegram answered a SaveFilePart/SaveBigFilePart call with False."""


async def save_part_with_retry(session, rpc, attempts: int = PART_MAX_ATTEMPTS,
                               flood_cap: float = PART_FLOOD_WAIT_CAP) -> None:
    """Save one part, retrying until Telegram confirms it; raise on final failure.

    Session.invoke only sleeps through flood waits up to its default threshold
    (10s). Non-premium accounts routinely get FLOOD_PREMIUM_WAIT_X above that on
    upload.SaveBigFilePart, so the wait has to be honoured here.
    """
    for attempt in range(1, attempts + 1):
        try:
            if await session.invoke(rpc):
                return
            error = UploadPartError(f"Telegram did not save upload part {rpc.file_part}")
            delay = min(2.0 ** attempt, PART_BACKOFF_CAP)
        except (FloodWait, FloodPremiumWait) as e:  # Not related by inheritance
            wait = float(e.value or 0)
            if wait > flood_cap or attempt == attempts:
                raise
            error, delay = e, wait + 1
        except BadRequest:
            # Invalid part size/number etc.: retrying cannot help
            raise
        except Exception as e:  # Timeouts, dropped connections, 5xx
            error, delay = e, min(2.0 ** attempt, PART_BACKOFF_CAP)

        if attempt == attempts:
            raise error

        log.warning("Upload part %s failed (%s/%s), retrying in %.0fs: %s: %s",
                    rpc.file_part, attempt, attempts, delay, type(error).__name__, error)
        await _sleep(delay)


def _count_resave(file_id: int, file_part: int) -> None:
    count = _resave_counts.pop(file_id, 0) + 1
    if count > MISSING_PART_RESAVE_LIMIT:
        log.warning("File %s re-saved parts more than %s times, giving up", file_id, MISSING_PART_RESAVE_LIMIT)
        raise FilePartMissing(value=file_part)
    _resave_counts[file_id] = count
    while len(_resave_counts) > 256:
        _resave_counts.popitem(last=False)


class SaveFile:
    async def save_file(
        self: "pyrogram.Client",
        path: Union[str, BinaryIO],
        file_id: int = None,
        file_part: int = 0,
        progress: Callable = None,
        progress_args: tuple = ()
    ):
        """Upload a file onto Telegram servers, without actually sending the message to anyone.
        Useful whenever an InputFile type is required.

        .. note::

            This is a utility method intended to be used **only** when working with raw
            :obj:`functions <pyrogram.api.functions>` (i.e: a Telegram API method you wish to use which is not
            available yet in the Client class as an easy-to-use method).

        .. include:: /_includes/usable-by/users-bots.rst

        Parameters:
            path (``str`` | ``BinaryIO``):
                The path of the file you want to upload that exists on your local machine or a binary file-like object
                with its attribute ".name" set for in-memory uploads.

            file_id (``int``, *optional*):
                In case a file part expired, pass the file_id and the file_part to retry uploading that specific chunk.

            file_part (``int``, *optional*):
                In case a file part expired, pass the file_id and the file_part to retry uploading that specific chunk.

            progress (``Callable``, *optional*):
                Pass a callback function to view the file transmission progress.
                The function must take *(current, total)* as positional arguments (look at Other Parameters below for a
                detailed description) and will be called back each time a new file chunk has been successfully
                transmitted.

            progress_args (``tuple``, *optional*):
                Extra custom arguments for the progress callback function.
                You can pass anything you need to be available in the progress callback scope; for example, a Message
                object or a Client instance in order to edit the message with the updated progress status.

        Other Parameters:
            current (``int``):
                The amount of bytes transmitted so far.

            total (``int``):
                The total size of the file.

            *args (``tuple``, *optional*):
                Extra custom arguments as defined in the ``progress_args`` parameter.
                You can either keep ``*args`` or add every single extra argument in your function signature.

        Returns:
            ``InputFile``: On success, the uploaded file is returned in form of an InputFile object.
            Every part is confirmed by Telegram before it is returned; a part that still fails after
            retries raises instead of yielding an incomplete file (FILE_PART_X_MISSING later on).

        Raises:
            RPCError: In case of a Telegram RPC error.
        """
        async with self.save_file_semaphore:
            if path is None:
                return None

            if isinstance(path, (str, PurePath)):
                fp = open(path, "rb")
            elif isinstance(path, io.IOBase):
                fp = path
            else:
                raise ValueError("Invalid file. Expected a file path as string or a binary (not text) file pointer")

            try:
                file_name = getattr(fp, "name", "file.jpg")

                fp.seek(0, os.SEEK_END)
                file_size = fp.tell()
                fp.seek(0)

                if file_size == 0:
                    raise ValueError("File size equals to 0 B")

                if self.me and self.me.is_premium:
                    file_size_limit_mib = 4000
                else:
                    file_size_limit_mib = 2000

                if file_size > file_size_limit_mib * 1024 * 1024:
                    raise ValueError(f"Can't upload files bigger than {file_size_limit_mib} MiB")

                file_total_parts = int(math.ceil(file_size / PART_SIZE))
                is_big = file_size > BIG_FILE_THRESHOLD
                is_missing_part = file_id is not None
                file_id = file_id or self.rnd_id()
                dc_id = await self.storage.dc_id()
                session = await self.get_session(dc_id, is_media=True)

                def make_rpc(part: int, chunk: bytes):
                    if is_big:
                        return raw.functions.upload.SaveBigFilePart(
                            file_id=file_id,
                            file_part=part,
                            file_total_parts=file_total_parts,
                            bytes=chunk
                        )
                    return raw.functions.upload.SaveFilePart(
                        file_id=file_id,
                        file_part=part,
                        bytes=chunk
                    )

                if is_missing_part:
                    # Re-save only the requested part (used by send_* on FILE_PART_X_MISSING)
                    _count_resave(file_id, file_part)
                    fp.seek(PART_SIZE * file_part)
                    chunk = fp.read(PART_SIZE)
                    if chunk:
                        await save_part_with_retry(session, make_rpc(file_part, chunk))
                    return None

                return await self._upload_all_parts(
                    session, fp, file_size, is_big, make_rpc,
                    file_id, file_total_parts, file_name, progress, progress_args
                )
            finally:
                if isinstance(path, (str, PurePath)):
                    fp.close()

    async def _upload_all_parts(
        self: "pyrogram.Client",
        session, fp, file_size: int, is_big: bool, make_rpc,
        file_id: int, file_total_parts: int, file_name: str,
        progress: Callable, progress_args: tuple
    ):
        workers_count = 4 if is_big else 1
        queue = asyncio.Queue(workers_count)
        failures = []
        md5_sum = None if is_big else md5()

        async def worker():
            while True:
                rpc = await queue.get()

                if rpc is None:
                    return

                if failures:
                    continue  # Already failed: just drain so the producer can stop

                try:
                    await save_part_with_retry(session, rpc)
                except Exception as e:
                    failures.append(e)

        workers = [self.loop.create_task(worker()) for _ in range(workers_count)]
        finished = False

        try:
            file_part = 0

            while not failures:
                chunk = fp.read(PART_SIZE)

                if not chunk:
                    break

                await queue.put(make_rpc(file_part, chunk))

                if md5_sum is not None:
                    md5_sum.update(chunk)

                file_part += 1

                if progress:
                    func = functools.partial(
                        progress,
                        min(file_part * PART_SIZE, file_size),
                        file_size,
                        *progress_args
                    )

                    if inspect.iscoroutinefunction(progress):
                        await func()
                    else:
                        await self.loop.run_in_executor(self.executor, func)

            for _ in workers:
                await queue.put(None)

            await asyncio.gather(*workers)
            finished = True
        finally:
            if not finished:
                # StopTransmission, cancellation or an unexpected error: stop uploading now
                for task in workers:
                    task.cancel()

                await asyncio.gather(*workers, return_exceptions=True)

        if failures:
            raise failures[0]

        if is_big:
            return raw.types.InputFileBig(
                id=file_id,
                parts=file_total_parts,
                name=file_name,
            )

        return raw.types.InputFile(
            id=file_id,
            parts=file_total_parts,
            name=file_name,
            md5_checksum=md5_sum.hexdigest()
        )
