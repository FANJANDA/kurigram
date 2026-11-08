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
from types import SimpleNamespace

import pytest

from pyrogram.session.session import AuthKeyNotFound, Session, SessionState


def _started_session() -> Session:
    """Only the fields `stop()` and `restart()` touch; no network."""
    session = Session.__new__(Session)

    async def close() -> None:
        pass

    async def disconnect_handler(*_args) -> None:
        pass

    session.client = SimpleNamespace(disconnect_handler=disconnect_handler)
    session.connection = SimpleNamespace(close=close)
    session._state = SessionState.STARTED
    session._state_lock = asyncio.Lock()
    session.restart_lock = asyncio.Lock()
    session.ignore_count = 0
    session.is_started = asyncio.Event()
    session.is_started.set()
    session.stored_msg_ids = []
    session.recent_msg_ids = []
    session.ping_task = None
    session.ping_task_event = asyncio.Event()
    session.recv_task = None
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize("worker", ["ping_task", "recv_task"])
async def test_a_restart_gets_past_a_worker_that_died(worker: str) -> None:
    session = _started_session()

    async def die() -> None:
        # What `recv_worker` raises when the server answers with a transport -404.
        raise AuthKeyNotFound("Auth key not found in the system.")

    setattr(session, worker, asyncio.ensure_future(die()))
    await asyncio.sleep(0)

    started = False

    async def start() -> None:
        nonlocal started
        started = True

    session.start = start

    # Before: `restart()` re-raised the worker's error, the session stayed `STOPPING`
    #  and `start()` never ran, so every later `invoke` timed out until the process restarted.
    await session.restart()

    assert started
    assert getattr(session, worker) is None
    assert session._state is SessionState.STOPPED
