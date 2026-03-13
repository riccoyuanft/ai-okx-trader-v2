import asyncio
from typing import Optional
from loguru import logger

from src.db.redis_client import set_engine_running, clear_engine_running, is_engine_running

_RECONNECT_DELAYS = [5, 15, 30, 60, 120, 300]  # backoff steps in seconds


_LOG_BUFFER_SIZE = 200  # lines per user


class UserEngineManager:
    def __init__(self):
        self.tasks: dict[str, asyncio.Task] = {}
        self.log_queues: dict[str, asyncio.Queue] = {}
        self.log_buffers: dict[str, list[str]] = {}   # circular replay buffer
        self._strategies: dict[str, dict] = {}       # stored for auto-reconnect
        self._engines: dict[str, object] = {}         # live UserEngine instances
        self._user_stopped: set[str] = set()          # user-initiated stops: no restart
        self._reconnect_attempt: dict[str, int] = {}  # crash count per user

    async def start_engine(self, user_id: str, strategy: dict) -> bool:
        if user_id in self.tasks and not self.tasks[user_id].done():
            logger.warning(f"[{user_id}] Engine already running")
            return False

        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.log_queues[user_id] = queue
        if user_id not in self.log_buffers:
            self.log_buffers[user_id] = []
        self._strategies[user_id] = strategy
        self._user_stopped.discard(user_id)

        from src.engine.user_engine import UserEngine
        engine = UserEngine(
            user_id=user_id,
            strategy=strategy,
            log_queue=queue,
            log_buffer=self.log_buffers[user_id],
            log_buffer_size=_LOG_BUFFER_SIZE,
        )
        self._engines[user_id] = engine

        task = asyncio.create_task(engine.run(), name=f"engine:{user_id}")
        self.tasks[user_id] = task
        task.add_done_callback(lambda t: self._on_task_done(user_id, t))

        await set_engine_running(user_id)
        logger.info(f"[{user_id}] Engine started (strategy: {strategy.get('name')})")
        return True

    async def stop_engine(self, user_id: str) -> bool:
        """User-initiated stop: cancel task and clear Redis state (no auto-restart)."""
        self._user_stopped.add(user_id)
        self._reconnect_attempt.pop(user_id, None)

        task = self.tasks.get(user_id)
        if not task or task.done():
            await clear_engine_running(user_id)
            self.log_queues.pop(user_id, None)
            return False

        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        await clear_engine_running(user_id)
        self.tasks.pop(user_id, None)
        self.log_queues.pop(user_id, None)
        self.log_buffers.pop(user_id, None)
        self._engines.pop(user_id, None)
        logger.info(f"[{user_id}] Engine stopped by user")
        return True

    def is_running(self, user_id: str) -> bool:
        task = self.tasks.get(user_id)
        return task is not None and not task.done()

    def get_log_buffer(self, user_id: str) -> list[str]:
        return list(self.log_buffers.get(user_id, []))

    def request_manual_close(self, user_id: str) -> bool:
        """Signal the running engine to close its position. Engine continues after close."""
        engine = self._engines.get(user_id)
        if engine:
            engine._manual_close_event.set()
            return True
        return False

    async def shutdown_all(self) -> None:
        """Graceful server shutdown: cancel tasks WITHOUT clearing Redis flags.
        Redis flags are preserved so engines are auto-restored on next startup."""
        user_ids = list(self.tasks.keys())
        for user_id in user_ids:
            self._user_stopped.add(user_id)  # prevent _on_task_done from restarting
            task = self.tasks.pop(user_id, None)
            if task and not task.done():
                task.cancel()
            self.log_queues.pop(user_id, None)
            self._engines.pop(user_id, None)
        # Wait briefly for cancellation
        await asyncio.sleep(0.5)
        logger.info(f"All engines shut down (Redis state preserved for {len(user_ids)} users)")

    def _on_task_done(self, user_id: str, task: asyncio.Task) -> None:
        self.tasks.pop(user_id, None)

        if task.cancelled() or user_id in self._user_stopped:
            return

        exc = task.exception()
        if exc:
            attempt = self._reconnect_attempt.get(user_id, 0)
            delay = _RECONNECT_DELAYS[min(attempt, len(_RECONNECT_DELAYS) - 1)]
            self._reconnect_attempt[user_id] = attempt + 1
            logger.error(f"[{user_id}] Engine crashed (attempt #{attempt + 1}): {exc} — reconnecting in {delay}s")
            try:
                asyncio.get_running_loop().create_task(
                    self._reconnect_after(user_id, delay),
                    name=f"reconnect:{user_id}",
                )
            except RuntimeError:
                logger.error(f"[{user_id}] Cannot schedule reconnect (no running loop)")

    async def _reconnect_after(self, user_id: str, delay: int) -> None:
        await asyncio.sleep(delay)
        if user_id in self._user_stopped:
            return
        strategy = self._strategies.get(user_id)
        if not strategy:
            logger.warning(f"[{user_id}] No strategy cached for reconnect, giving up")
            return
        logger.info(f"[{user_id}] Auto-reconnecting engine (strategy: {strategy.get('name')})")
        await self.start_engine(user_id, strategy)


_manager: Optional[UserEngineManager] = None


def get_manager() -> UserEngineManager:
    global _manager
    if _manager is None:
        _manager = UserEngineManager()
    return _manager
