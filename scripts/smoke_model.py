"""Opt-in real visual model check on a synthetic screen; no remote desktop involved."""

import argparse
import asyncio
import time
from io import BytesIO

from PIL import Image, ImageDraw
from pydantic import BaseModel

from app.core.config import Settings
from app.models.factory import build_visual_model
from app.models.openrouter_visual import ProviderCallError


class SeenValue(BaseModel):
    count: int


async def smoke(env_file, model, role="verifier"):
    settings = Settings(_env_file=env_file)
    if model:
        settings = settings.model_copy(update={"model_" + role: model})
    image = Image.new("RGB", (1280, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.text((100, 100), "SYNTHETIC TEST SCREEN", fill="black", font_size=48)
    draw.text((100, 220), "Invoice count: 7", fill="black", font_size=48)
    stream = BytesIO()
    image.save(stream, "PNG")
    client = build_visual_model(settings)
    await client.validate_capabilities()
    start = time.monotonic()
    result = await client.decide(
        role,
        SeenValue,
        {"instruction": "Read the invoice count in the supplied synthetic screenshot."},
        stream.getvalue(),
    )
    if result.count != 7:
        raise RuntimeError("Visual smoke did not match synthetic ground truth")
    print(
        f"Vision smoke passed; role={role}, model={client.profiles.for_role(role).model}, latency_s={time.monotonic() - start:.2f}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=None, help="Ignored local config; never paste keys")
    parser.add_argument("--model", default=None)
    parser.add_argument("--role", choices=("coordinator", "operator", "verifier", "recovery", "analyst"), default="verifier")
    args = parser.parse_args()
    try:
        asyncio.run(smoke(args.env_file, args.model, args.role))
    except ProviderCallError as exc:
        print(f"Vision smoke unavailable: OpenRouter HTTP {exc.status_code}; no provider payload printed")
        return 1
    except Exception as exc:
        print(
            f"Vision smoke unavailable ({type(exc).__name__}); configure provider key/model or inspect privately."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
