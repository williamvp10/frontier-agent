"""Provision the metadata-only dashboard via Langfuse CLI; optional synthetic smoke.

Credentials remain in subprocess environment. Responses/errors are parsed privately
and never printed. No model or remote desktop is accessed by this script.
"""

import argparse
import asyncio
import json
import os
import subprocess
import time
from uuid import uuid4

from app.core.config import Settings
from app.core.observability import Observability, count, observed, progress, record_usage

CLI = ["npx", "--yes", "@langfuse/cli@1.2.4", "api"]
DASHBOARD_NAME = "Frontier - observabilidad v1"


class SetupUnavailable(RuntimeError):
    pass


def cli_environment(settings):
    public = settings.langfuse_public_key.get_secret_value()
    secret = settings.langfuse_secret_key.get_secret_value()
    if not public.strip() or not secret.strip():
        raise SetupUnavailable("missing_credentials")
    return {**os.environ, "LANGFUSE_PUBLIC_KEY": public, "LANGFUSE_SECRET_KEY": secret,
            "LANGFUSE_BASE_URL": settings.langfuse_base_url,
            "LANGFUSE_HOST": settings.langfuse_base_url}


def cli_call(env, resource, action, *args, body=None):
    command = [*CLI, resource, action, *args, "--json"]
    if body is not None:
        # Definitions contain only public names, never credentials or task content.
        command.extend(["--body-json", json.dumps(body)])
    try:
        for attempt in range(4):
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
            payload = json.loads(result.stdout)
            if isinstance(payload, dict) and payload.get("status") == 429 and attempt < 3:
                time.sleep(30)
                continue
            break
        if isinstance(payload, dict) and "status" in payload and "body" in payload:
            if not 200 <= payload["status"] < 300:
                raise SetupUnavailable(resource + "_" + action + "_http_" + str(payload["status"]))
            payload = payload["body"]
        if result.returncode:
            raise SetupUnavailable(resource + "_" + action + "_failed")
        # CLI's stable envelope carries the actual API response in `data`.
        if isinstance(payload, dict) and ("success" in payload or "ok" in payload):
            if payload.get("success", payload.get("ok")) is False:
                raise SetupUnavailable("cli_request_failed")
            payload = payload["data"]
        return payload
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        raise SetupUnavailable("cli_unavailable") from None


def items(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    raise SetupUnavailable("unexpected_api_schema")


def name_filter(name):
    return {"column": "name", "operator": "=", "value": name, "type": "string"}


def widget_definitions():
    definitions = []
    scores = (
        ("Tiempo total P95 (s)", "task_duration_seconds", "p95"),
        ("Cola media (s)", "queue_wait_seconds", "avg"),
        ("Llamadas modelo / tarea", "model_calls", "avg"),
        ("Llamadas Jev / tarea", "jev_calls", "avg"),
        ("Llamadas VNC / tarea", "vnc_calls", "avg"),
        ("Falta de progreso", "no_progress_events", "sum"),
        ("Racha sin progreso P95", "no_progress_max_streak", "p95"),
        ("Escalamientos recuperación", "recovery_escalations", "sum"),
        ("Escalamientos Jev", "jev_escalations", "sum"),
    )
    for title, score, aggregation in scores:
        definitions.append({"name": "Frontier v1 - " + title,
            "description": "Metadatos seguros; score " + score,
            "view": "scores-numeric", "chartType": "NUMBER", "dimensions": [],
            "metrics": [{"measure": "value", "agg": aggregation}],
            "filters": [name_filter(score)]})
    definitions.extend([
        {"name": "Frontier v1 - Tareas", "view": "observations", "chartType": "NUMBER",
         "dimensions": [], "metrics": [{"measure": "count", "agg": "count"}],
         "filters": [name_filter("execute-task")]},
        {"name": "Frontier v1 - Éxito cerrado (0-1)", "view": "scores-boolean",
         "chartType": "NUMBER", "dimensions": [],
         "metrics": [{"measure": "value", "agg": "avg"}],
         "filters": [name_filter("task_success")]},
        {"name": "Frontier v1 - Latencia P95 por operación", "view": "observations",
         "chartType": "HORIZONTAL_BAR", "dimensions": [{"field": "name"}],
         "metrics": [{"measure": "latency", "agg": "p95"}], "filters": []},
        {"name": "Frontier v1 - Coste por modelo", "view": "observations",
         "chartType": "HORIZONTAL_BAR", "dimensions": [{"field": "providedModelName"}],
         "metrics": [{"measure": "totalCost", "agg": "sum"}], "filters": []},
    ])
    return definitions


def provision(settings):
    env = cli_environment(settings)
    dashboards = items(cli_call(env, "unstable-dashboards", "list", "--all"))
    dashboard = next((item for item in dashboards if item["name"] == DASHBOARD_NAME), None)
    if dashboard is None:
        dashboard = cli_call(env, "unstable-dashboards", "create", body={"name": DASHBOARD_NAME,
            "description": "Tiempo, llamadas, progreso y escalamiento; sin contexto privado."})
    dashboard_id = dashboard["id"]
    detail = cli_call(env, "unstable-dashboards", "get", dashboard_id)
    placements = detail.get("definition", {}).get("widgets", [])
    existing = {item["id"] for item in placements}
    widgets = items(cli_call(env, "unstable-dashboard-widgets", "list", "--all"))
    for index, definition in enumerate(widget_definitions()):
        widget = next((item for item in widgets if item["name"] == definition["name"]), None)
        if widget is None:
            widget = cli_call(env, "unstable-dashboard-widgets", "create", body=definition)
        placement_id = "frontier-observability-v1-" + str(index)
        if placement_id not in existing:
            cli_call(env, "unstable-dashboards", "add-placement", dashboard_id, body={
                "type": "widget", "id": placement_id, "widgetId": widget["id"],
                "x": index % 3 * 4, "y": index // 3 * 4, "width": 4, "height": 4})
    final = cli_call(env, "unstable-dashboards", "get", dashboard_id)
    actual_ids = {item["id"] for item in final.get("definition", {}).get("widgets", [])}
    expected_ids = {"frontier-observability-v1-" + str(index) for index in range(len(widget_definitions()))}
    if not expected_ids <= actual_ids:
        raise SetupUnavailable("dashboard_placements_not_confirmed")
    return len(widget_definitions())


@observed("generate-decision", as_type="generation", counter="model_calls")
async def synthetic_generation():
    record_usage({"input_tokens": 0, "output_tokens": 0, "cost_usd": 0}, model="openai/gpt-6-luna")


@observed("delegate-agent", as_type="agent", counter="agent_calls")
async def synthetic_agent():
    await synthetic_generation()


@observed("observe-vnc", as_type="tool", counter="vnc_calls")
async def synthetic_vnc():
    pass


async def smoke(settings):
    # The test environment keeps synthetic telemetry out of production views.
    settings = settings.model_copy(update={"langfuse_tracing_environment": "test", "langfuse_enabled": True})
    env = cli_environment(settings)
    telemetry = Observability.from_settings(settings)
    if telemetry.client is None:
        raise SetupUnavailable("sdk_unavailable")
    task_id = uuid4()
    trace_id = telemetry.client.create_trace_id(seed=str(task_id))
    try:
        with telemetry.task(task_id, uuid4(), "context-v2") as run:
            await synthetic_agent()
            await synthetic_vnc()
            count("recovery_escalations")
            progress(advanced=False)
            progress(advanced=True)
            telemetry.finish(run, state="terminada", queue_wait=0, task_duration=0, run_duration=0)
        await asyncio.to_thread(telemetry.client.flush)
        for _ in range(10):
            payload = await asyncio.to_thread(cli_call, env, "observations", "list",
                "--trace-id", trace_id, "--fields", "core,basic,usage,metadata,io", "--limit", "20")
            observations = items(payload)
            names = {item.get("name") for item in observations}
            if {"execute-task", "delegate-agent", "generate-decision", "observe-vnc"} <= names:
                by_name = {item["name"]: item for item in observations}
                root, agent, generation = (by_name[name] for name in (
                    "execute-task", "delegate-agent", "generate-decision"))
                if agent["parentObservationId"] != root["id"] or generation["parentObservationId"] != agent["id"]:
                    raise SetupUnavailable("invalid_remote_hierarchy")
                if any(item.get("input") not in (None, "", {}) for item in observations):
                    raise SetupUnavailable("unexpected_remote_input")
                return len(observations)
            await asyncio.sleep(3)
        raise SetupUnavailable("ingestion_not_confirmed")
    finally:
        await telemetry.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--apply", action="store_true", help="Create/reuse dashboard and widgets")
    parser.add_argument("--smoke", action="store_true", help="Upload and verify synthetic test trace")
    args = parser.parse_args()
    try:
        settings = Settings(_env_file=args.env_file)
        if args.smoke:
            observations = asyncio.run(smoke(settings))
            print(f"Langfuse synthetic smoke confirmed: {observations} observations (environment=test).")
        if args.apply:
            widgets = provision(settings)
            print(f"Langfuse dashboard ready: {DASHBOARD_NAME}; {widgets} widgets.")
        if not args.apply and not args.smoke:
            print(f"Plan: {DASHBOARD_NAME}; {len(widget_definitions())} widgets. Use --apply / --smoke.")
        return 0
    except Exception as exc:
        code = str(exc) if isinstance(exc, SetupUnavailable) else "setup_failed"
        print(f"Langfuse setup unavailable ({code}); private responses withheld.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
