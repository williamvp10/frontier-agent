import asyncio
import hmac
import ipaddress
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.domain.contracts import TaskAccepted, TaskRequest, TaskResponse
from app.persistence.database import database_pool
from app.persistence.repositories import HeartbeatRepository, TaskRepository
from app.persistence.secrets import CredentialCipher


class Readiness:
    def __init__(self, sessions, redis: Redis, *, max_age: float = 10, require_memory: bool = True):
        self.sessions, self.redis = sessions, redis
        self.heartbeats = HeartbeatRepository(sessions)
        self.max_age, self.require_memory = max_age, require_memory

    async def __call__(self) -> bool:
        async def check():
            async with self.sessions() as db:
                await db.execute(text("SELECT 1"))
                if self.require_memory:
                    initialized = await db.scalar(
                        text(
                            "SELECT to_regclass('graph_checkpoints.checkpoints') IS NOT NULL AND to_regclass('graph_store.store') IS NOT NULL"
                        )
                    )
                    if not initialized:
                        return False
            return bool(await self.redis.ping()) and await self.heartbeats.ready(
                window_seconds=self.max_age
            )

        try:
            async with asyncio.timeout(0.7):
                return await check()
        except (SQLAlchemyError, RedisError, TimeoutError, OSError):
            # Never reflect driver errors, DSNs or credentials to public responses.
            return False


def create_app(settings: Settings | None = None, *, repository=None, readiness=None) -> FastAPI:
    settings = settings or Settings()
    token = settings.team_entrypoint_token.get_secret_value()
    if not token:
        raise ValueError("TEAM_ENTRYPOINT_TOKEN is required")
    engine = redis = None
    if repository is None:
        engine, sessions = database_pool(settings.database_url.get_secret_value())
        cipher = CredentialCipher(
            settings.credentials_encryption_key.get_secret_value(),
            settings.destination_hmac_key.get_secret_value(),
        )
        repository = TaskRepository(
            sessions,
            cipher,
            cutoff_seconds=settings.action_cutoff_seconds,
            close_seconds=settings.close_target_seconds,
            task_seconds=settings.task_seconds,
        )
        redis = Redis.from_url(
            settings.redis_url.get_secret_value(),
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        readiness = readiness or Readiness(
            sessions, redis, max_age=settings.heartbeat_max_age_seconds
        )
    if readiness is None:
        raise ValueError("Readiness must be supplied with an injected repository")

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if redis is not None:
                await redis.aclose()
            if engine is not None:
                await engine.dispose()

    app = FastAPI(
        title="Frontier Agent",
        description="API de ejecución de tareas sobre escritorios VNC. "
        "Los endpoints requieren el token del equipo mediante Authorization: Bearer <token>.",
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
        swagger_ui_oauth2_redirect_url=None,
        # Describe authentication in OpenAPI; middleware remains its authority.
        dependencies=[Depends(HTTPBearer(
            auto_error=False,
            scheme_name="TeamBearer",
            description="Token TEAM_ENTRYPOINT_TOKEN del equipo; introducir sólo el token.",
        ))],
        lifespan=lifespan,
    )
    app.state.repository, app.state.readiness = repository, readiness

    @app.middleware("http")
    async def auth_and_limits(request: Request, call_next):
        # Exact documentation paths only; no prefix exemption for operational routes.
        if request.method in {"GET", "HEAD"} and request.url.path in {
            app.docs_url, app.openapi_url,
        }:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        provided = header[7:] if header.startswith("Bearer ") else ""
        if not provided or not hmac.compare_digest(provided.encode(), token.encode()):
            return JSONResponse(
                {"detail": "Unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
        request.state.accepted_at = datetime.now(UTC)
        if request.method == "POST":
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 32768:
                    return JSONResponse({"detail": "Request body exceeds limit"}, status_code=422)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Pydantic's default errors include raw inputs, URL and password.
        return JSONResponse({"detail": "Invalid request"}, status_code=422)

    @app.get("/health", tags=["Estado"], summary="Comprobar disponibilidad del sistema")
    async def health():
        """Devuelve 200 cuando dependencias, memoria y heartbeats están listos; 503 si no."""
        if not await readiness():
            return JSONResponse({"ok": False}, status_code=503)
        return {"ok": True}

    @app.post("/tasks", response_model=TaskAccepted, tags=["Tareas"], summary="Crear una tarea")
    async def tasks(body: TaskRequest, request: Request):
        """Acepta tarea y escritorio con persistencia durable; devuelve su UUID sin esperar VNC."""
        if not settings.allow_private_desktops:
            hostname = urlsplit(body.escritorio.url).hostname or ""
            if hostname.lower().rstrip(".") == "localhost":
                return JSONResponse(
                    {"detail": "Desktop destination is not allowed"}, status_code=422
                )
            try:
                address = ipaddress.ip_address(hostname)
            except ValueError:
                address = None
            if address is not None and not address.is_global:
                return JSONResponse(
                    {"detail": "Desktop destination is not allowed"}, status_code=422
                )
            # DNS and the actual connected peer are checked again by transport;
            # admission never opens a desktop or treats textual validation as DNS safety.
        try:
            async with asyncio.timeout(settings.admission_timeout_seconds):
                if not await readiness():
                    return JSONResponse({"detail": "Service not ready"}, status_code=503)
                task_id = await repository.accept_with_outbox(
                    instruction=body.tarea,
                    url=body.escritorio.url,
                    password=body.escritorio.password.get_secret_value(),
                    team_id=settings.team_id,
                    accepted_at=request.state.accepted_at,
                )
                return TaskAccepted(task_id=task_id)
        except (SQLAlchemyError, RedisError, TimeoutError, OSError):
            return JSONResponse({"detail": "Task admission unavailable"}, status_code=503)

    @app.get("/tasks/{task_id}", response_model=TaskResponse, tags=["Tareas"],
             summary="Consultar estado y resumen de una tarea")
    async def task(task_id: UUID):
        """Consulta una tarea del equipo autenticado; devuelve 404 si no existe para ese equipo."""
        try:
            async with asyncio.timeout(settings.admission_timeout_seconds):
                result = await repository.get_for_team(task_id, settings.team_id)
        except (SQLAlchemyError, RedisError, TimeoutError, OSError):
            return JSONResponse({"detail": "Task state unavailable"}, status_code=503)
        if result is None:
            return JSONResponse({"detail": "Task not found"}, status_code=404)
        return result

    return app
