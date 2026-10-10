from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from redis.exceptions import ResponseError
from sqlalchemy import or_, select, update

from app.persistence.models import Outbox
from app.persistence.repositories import utcnow


@dataclass(frozen=True)
class QueueMessage:
    message_id: str
    task_id: UUID
    outbox_id: UUID


class TaskStream:
    def __init__(self, redis, *, stream: str = "frontier:tasks", group: str = "frontier:workers"):
        self.redis, self.stream, self.group = redis, stream, group

    async def setup(self):
        try:
            await self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def publish(self, task_id: UUID, outbox_id: UUID):
        return await self.redis.xadd(
            self.stream, {"task_id": str(task_id), "outbox_id": str(outbox_id)}
        )

    @staticmethod
    def _message(raw) -> QueueMessage:
        message_id, fields = raw

        def decoded(value):
            return value.decode() if isinstance(value, bytes) else value

        fields = {decoded(k): decoded(v) for k, v in fields.items()}
        return QueueMessage(decoded(message_id), UUID(fields["task_id"]), UUID(fields["outbox_id"]))

    async def read(
        self, consumer: str, *, block_ms: int = 500, count: int = 1
    ) -> list[QueueMessage]:
        rows = await self.redis.xreadgroup(
            self.group, consumer, {self.stream: ">"}, count=count, block=block_ms
        )
        return [self._message(row) for _, messages in rows for row in messages]

    async def reclaim(
        self, consumer: str, *, idle_ms: int = 10000, count: int = 10
    ) -> list[QueueMessage]:
        # Redis >=6.2; group pending IDs survive a worker process crash.
        result = await self.redis.xautoclaim(
            self.stream, self.group, consumer, min_idle_time=idle_ms, start_id="0-0", count=count
        )
        return [self._message(row) for row in result[1]]

    async def ack(self, message: QueueMessage):
        await self.redis.xack(self.stream, self.group, message.message_id)

    async def claim_and_ack(self, message: QueueMessage, repository, worker_id: str, **versions):
        # If DB claim raises, pending remains. Claim commits before ACK.
        claim = await repository.claim(message.task_id, worker_id, **versions)
        await self.ack(message)
        return claim


class OutboxDispatcher:
    def __init__(self, sessions, stream: TaskStream, instance_id: str, *, lease_seconds: int = 10):
        self.sessions, self.stream, self.instance_id = sessions, stream, instance_id
        self.lease_seconds = lease_seconds

    async def dispatch_once(self, *, batch_size: int = 20) -> int:
        now = utcnow()
        async with self.sessions.begin() as db:
            rows = list(
                (
                    await db.scalars(
                        select(Outbox)
                        .where(
                            Outbox.published_at.is_(None),
                            Outbox.available_at <= now,
                            or_(Outbox.claimed_until.is_(None), Outbox.claimed_until < now),
                        )
                        .order_by(Outbox.created_at)
                        .limit(batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            claims = [(row.id, row.task_id) for row in rows]
            for row in rows:
                row.claimed_by, row.claimed_until = (
                    self.instance_id,
                    now + timedelta(seconds=self.lease_seconds),
                )
                row.attempts += 1
        published = 0
        for outbox_id, task_id in claims:
            await self.stream.publish(task_id, outbox_id)
            async with self.sessions.begin() as db:
                await db.execute(
                    update(Outbox)
                    .where(
                        Outbox.id == outbox_id,
                        Outbox.claimed_by == self.instance_id,
                        Outbox.published_at.is_(None),
                    )
                    .values(published_at=utcnow(), claimed_until=None)
                )
            published += 1
        return published
