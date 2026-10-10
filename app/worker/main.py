"""Sequential demo worker. Run: python -m app.worker.main."""

import asyncio
import signal
from contextlib import suppress
from uuid import uuid4

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.graph_memory import graph_memory
from app.models.factory import build_visual_model
from app.persistence.database import database_pool
from app.persistence.repositories import (
    EventRecorder,
    EvidenceRepository,
    HeartbeatRepository,
    SessionRepository,
    TaskRepository,
)
from app.persistence.secrets import CredentialCipher
from app.queue.streams import TaskStream
from app.worker.runner import TaskRunner


async def serve(settings=None):
    settings = settings or Settings()
    model = build_visual_model(settings)
    await model.validate_capabilities()
    engine, sessions = database_pool(settings.database_url.get_secret_value())
    redis = Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
    cipher = CredentialCipher(
        settings.credentials_encryption_key.get_secret_value(),
        settings.destination_hmac_key.get_secret_value(),
    )
    tasks = TaskRepository(sessions, cipher)
    hb = HeartbeatRepository(sessions)
    identity = uuid4().hex
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    ready = False

    async def heartbeat():
        while not stop.is_set():
            try:
                for component in ("worker", "gateway", "supervisor"):
                    await hb.beat(component, identity, ready=ready)
                if runner is not None:
                    for run_id in list(runner.active_runs):
                        await tasks.heartbeat_run(run_id)
                    await tasks.expire_queued()
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop.wait(), 2)
            except TimeoutError:
                pass

    beating = asyncio.create_task(heartbeat())
    runner = None
    try:
        stream = TaskStream(redis)
        await stream.setup()
        async with graph_memory(settings.langgraph_database_uri.get_secret_value()) as (
            saver,
            store,
        ):
            runner = TaskRunner(
                settings,
                tasks,
                SessionRepository(sessions),
                EvidenceRepository(sessions),
                EventRecorder(sessions),
                model,
                checkpointer=saver,
            )
            # Store is initialized and available for later allowed generic knowledge only;
            # this evaluation worker intentionally does not reuse business data across runs.
            ready = True
            while not stop.is_set():
                messages = await stream.reclaim(identity, count=1)
                if not messages:
                    messages = await stream.read(identity, count=1)
                for message in messages:
                    if stop.is_set():
                        break
                    claim = await stream.claim_and_ack(
                        message, tasks, identity, model_version=settings.model_operator,
                        graph_version=settings.graph_version,
                    )
                    if claim:
                        running = asyncio.create_task(runner.run(claim, identity))
                        stopping = asyncio.create_task(stop.wait())
                        done, _ = await asyncio.wait(
                            [running, stopping], return_when=asyncio.FIRST_COMPLETED
                        )
                        stopping.cancel()
                        if stopping in done:
                            running.cancel()
                        try:
                            await running
                        except Exception:
                            pass
    finally:
        ready = False
        stop.set()
        if runner:
            await runner.close_all()
        beating.cancel()
        with suppress(asyncio.CancelledError):
            await beating
        for component in ("worker", "gateway", "supervisor"):
            with suppress(Exception):
                await hb.beat(component, identity, ready=False)
        await redis.aclose()
        await engine.dispose()
        if runner:
            await runner.observability.shutdown()


def main():
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"Worker unavailable ({type(exc).__name__}); private details withheld.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
