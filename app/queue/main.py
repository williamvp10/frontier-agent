"""Outbox publisher service; bootstrap creates the consumer group once."""

import asyncio
import logging
import signal
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.persistence.database import database_pool
from app.queue.streams import OutboxDispatcher, TaskStream


async def run():
    settings = Settings()
    engine, sessions = database_pool(settings.database_url.get_secret_value(), pool_size=2)
    redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    stream = TaskStream(redis)
    dispatcher = OutboxDispatcher(sessions, stream, "dispatcher-" + uuid4().hex)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await stream.setup()
        while not stop.is_set():
            try:
                await dispatcher.dispatch_once()
            except (SQLAlchemyError, RedisError, TimeoutError, OSError):
                # Durable outbox retries; never print raw DB/Redis exceptions.
                logging.getLogger(__name__).warning(
                    "Outbox delivery unavailable; durable publication will retry"
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.25)
            except TimeoutError:
                pass
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except (SQLAlchemyError, RedisError, TimeoutError, OSError, ValueError):
        raise SystemExit("Dispatcher startup unavailable; review private configuration") from None
