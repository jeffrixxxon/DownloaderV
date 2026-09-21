"""Очередь загрузок: ограничивает параллелизм и число задач на пользователя."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

from .config import config

log = logging.getLogger(__name__)


class QueueFull(Exception):
    """У пользователя уже слишком много задач в работе."""


class JobQueue:
    def __init__(self, workers: int, per_user_limit: int) -> None:
        self._queue: asyncio.Queue[tuple[int, Callable[[], Awaitable[None]]]] = asyncio.Queue()
        self._workers_count = max(1, workers)
        self._per_user_limit = max(1, per_user_limit)
        self._per_user: dict[int, int] = defaultdict(int)
        self._tasks: list[asyncio.Task[None]] = []

    # -- жизненный цикл ----------------------------------------------------- #

    def start(self) -> None:
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(self._worker(i), name=f"dl-worker-{i}")
            for i in range(self._workers_count)
        ]
        log.info("Запущено воркеров загрузки: %s", self._workers_count)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # -- API ---------------------------------------------------------------- #

    def submit(self, user_id: int, job: Callable[[], Awaitable[None]]) -> int:
        """Ставит задачу в очередь. Возвращает позицию (0 = сразу в работу)."""
        if self._per_user[user_id] >= self._per_user_limit:
            raise QueueFull
        self._per_user[user_id] += 1
        self._queue.put_nowait((user_id, job))
        return max(0, self._queue.qsize() - self._workers_count)

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    # -- внутреннее --------------------------------------------------------- #

    async def _worker(self, index: int) -> None:
        while True:
            user_id, job = await self._queue.get()
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Воркер %s: задача упала", index)
            finally:
                self._per_user[user_id] = max(0, self._per_user[user_id] - 1)
                if self._per_user[user_id] == 0:
                    self._per_user.pop(user_id, None)
                self._queue.task_done()


job_queue = JobQueue(config.max_concurrent, config.max_user_queue)
