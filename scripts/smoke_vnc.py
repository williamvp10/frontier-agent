"""Capture-only real VNC smoke through the isolated process, with private config."""

import argparse
import asyncio
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values

from app.desktop.gateway import ProcessGateway
from app.domain.contracts import utcnow


async def smoke(env_file):
    values = dotenv_values(env_file)
    url, password = values.get("VNC_WS_URL"), values.get("VNC_PASSWORD")
    if not url or not password:
        raise RuntimeError("Private VNC config incomplete")
    session_id = uuid4()
    gateway = ProcessGateway(session_id, 1, utcnow() + timedelta(seconds=30))
    try:
        await gateway.open(url, password)
        frame = await gateway.request("observe")
        target = Path(".private/evidencias") / f"service-smoke-{session_id}.png"
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        import os

        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(frame["image"])
        proof = await gateway.close_and_confirm()
        print(
            json.dumps(
                {
                    "ok": True,
                    "width": frame["width"],
                    "height": frame["height"],
                    "sha256": hashlib.sha256(frame["image"]).hexdigest(),
                    "owner_stopped": proof["owner_stopped"],
                }
            )
        )
    finally:
        await gateway.close_and_confirm()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.vnc")
    args = parser.parse_args()
    try:
        asyncio.run(smoke(args.env_file))
    except Exception as exc:
        print(f"VNC smoke failed ({type(exc).__name__}); private details withheld.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
