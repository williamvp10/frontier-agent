from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.application import create_app
from app.core.config import Settings

pytestmark = [pytest.mark.contract, pytest.mark.asyncio]


class Repo:
    def __init__(self):
        self.tasks = {}

    async def accept_with_outbox(self, **values):
        key = uuid4()
        self.tasks[key] = (values, {"estado": "en_curso", "resumen": ""})
        return key

    async def get_for_team(self, key, team):
        value = self.tasks.get(key)
        return value[1] if value and value[0]["team_id"] == team else None


@pytest.fixture
async def client():
    repo = Repo()

    async def ready():
        return True

    app = create_app(
        Settings(team_id="test", team_entrypoint_token="canary-bearer"),
        repository=repo,
        readiness=ready,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, repo


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/health"),
        ("post", "/tasks"),
        ("get", "/tasks/" + str(uuid4())),
        ("get", "/missing"),
        ("get", "/docs/private"),
        ("get", "/openapi.json/private"),
        ("post", "/docs"),
        ("post", "/openapi.json"),
        ("get", "/redoc"),
    ],
)
async def test_c01_operational_and_non_documentation_routes_require_bearer(client, method, path):
    http, _ = client
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        response = await getattr(http, method)(path, headers=headers)
        assert response.status_code == 401
        assert "wrong" not in response.text


async def test_swagger_and_schema_are_public_but_operations_require_authorization(client):
    http, repo = client
    docs = await http.get("/docs")
    assert docs.status_code == 200
    assert "text/html" in docs.headers["content-type"]
    assert "SwaggerUIBundle" in docs.text
    assert "/openapi.json" in docs.text
    response = await http.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert set(schema["paths"]) == {"/health", "/tasks", "/tasks/{task_id}"}
    bearer = schema["components"]["securitySchemes"]["TeamBearer"]
    assert bearer["type"] == "http"
    assert bearer["scheme"] == "bearer"
    for path, method in (("/health", "get"), ("/tasks", "post"), ("/tasks/{task_id}", "get")):
        assert schema["paths"][path][method]["security"] == [{"TeamBearer": []}]
    assert "canary-bearer" not in response.text + docs.text
    # Visiting public documentation does not authorize a subsequent operation.
    assert (await http.get("/health")).status_code == 401
    assert (await http.post("/tasks", json={})).status_code == 401
    assert repo.tasks == {}
    assert (await http.get("/health", headers={
        "Authorization": "Bearer canary-bearer",
    })).status_code == 200


async def test_c02_body_validation_never_reflects_secrets(client):
    http, repo = client
    headers = {"Authorization": "Bearer canary-bearer"}
    body = {
        "tarea": "Count",
        "escritorio": {"url": "wss://new.example/path", "password": "canary-password"},
    }
    response = await http.post("/tasks", json=body, headers=headers)
    assert response.status_code == 200
    task_id = UUID(response.json()["task_id"])
    assert repo.tasks[task_id][0]["url"] == "wss://new.example/path"
    assert repo.tasks[task_id][0]["password"] == "canary-password"
    for invalid in (
        {**body, "extra": True},
        {
            **body,
            "escritorio": {
                **body["escritorio"],
                "url": "wss://user:canary-password@new.example/path",
            },
        },
        {**body, "tarea": "x" * 17000},
    ):
        error = await http.post("/tasks", json=invalid, headers=headers)
        assert error.status_code == 422
        assert "canary-password" not in error.text
        assert "new.example" not in error.text
    too_large = await http.post("/tasks", content=b"x" * 33000, headers=headers)
    assert too_large.status_code == 422


async def test_c04_state_uuid_and_missing(client):
    http, _ = client
    headers = {"Authorization": "Bearer canary-bearer"}
    assert (await http.get("/tasks/not-uuid", headers=headers)).status_code == 422
    assert (await http.get("/tasks/" + str(uuid4()), headers=headers)).status_code == 404
    assert (await http.get("/health", headers=headers)).json() == {"ok": True}


@pytest.mark.parametrize(
    "url",
    [
        "wss://127.0.0.1/websockify",
        "wss://[::1]/websockify",
        "wss://169.254.169.254/websockify",
        "wss://localhost/websockify",
    ],
)
async def test_private_literal_destinations_blocked(client, url):
    http, repo = client
    response = await http.post(
        "/tasks",
        headers={"Authorization": "Bearer canary-bearer"},
        json={"tarea": "Count", "escritorio": {"url": url, "password": "canary-password"}},
    )
    assert response.status_code == 422
    assert repo.tasks == {}
    assert url not in response.text


@pytest.mark.parametrize("state", ["en_curso", "terminada", "fallida"])
async def test_public_shape_and_states_are_preserved(client, state):
    import time

    http, repo = client
    headers = {"Authorization": "Bearer canary-bearer"}
    start = time.monotonic()
    accepted = await http.post("/tasks", headers=headers, json={
        "tarea": "Consulta el dato solicitado.",
        "escritorio": {"url": "wss://unseen.example/custom-vnc", "password": "canary-password"},
    })
    assert time.monotonic() - start < 2
    assert accepted.status_code == 200
    assert set(accepted.json()) == {"task_id"}
    task_id = UUID(accepted.json()["task_id"])
    repo.tasks[task_id][1].update(estado=state, resumen="respuesta: valor" if state == "terminada" else "")
    response = await http.get(f"/tasks/{task_id}", headers=headers)
    assert response.status_code == 200
    assert response.json() == repo.tasks[task_id][1]
    assert set(response.json()) == {"estado", "resumen"}


async def test_not_ready_never_accepts_task():
    repo = Repo()

    async def not_ready():
        return False

    app = create_app(Settings(team_entrypoint_token="canary-bearer"),
                     repository=repo, readiness=not_ready)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": "Bearer canary-bearer"}) as http:
        assert (await http.get("/health")).status_code == 503
        response = await http.post("/tasks", json={"tarea": "Read",
            "escritorio": {"url": "wss://unseen.example/vnc", "password": "canary-password"}})
        assert response.status_code == 503
    assert repo.tasks == {}
