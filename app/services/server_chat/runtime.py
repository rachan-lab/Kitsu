"""Own one loopback-only CUDA inference process; never start the Discord bot."""

import asyncio
import logging
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

import aiohttp


ROOT = Path(__file__).resolve().parents[3]
logger = logging.getLogger(__name__)


def local_url(value):
    parsed = urlsplit(value.rstrip("/"))
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Server chat requires a local llama.cpp HTTP endpoint.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Configure only the loopback origin for server chat.")
    return value.rstrip("/")


class LocalRuntime:
    def __init__(self, config):
        self.url = local_url(config.server_chat_url)
        self.autostart = config.server_chat_autostart
        self.binary = ROOT / config.server_chat_runtime_path
        self.model = ROOT / config.server_chat_model_path
        self.lora = ROOT / config.server_chat_lora_path if getattr(config, "server_chat_lora_path", "") else None
        self.process = None
        self.log = None
        self.ready = False

    async def health(self):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1), trust_env=False) as session:
                async with session.get(self.url + "/health") as response:
                    data = await response.json()
                    if response.status != 200 or data.get("status") != "ok":
                        return False
                async with session.get(self.url + "/v1/models") as response:
                    data = await response.json()
                    return response.status == 200 and any(model.get("id") == "kitsu-server-chat" for model in data.get("data", []))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return False

    def arguments(self):
        parsed = urlsplit(self.url)
        args = [
            str(self.binary), "--model", str(self.model), "--alias", "kitsu-server-chat",
            "--host", parsed.hostname, "--port", str(parsed.port or 80),
            "--device", "CUDA0", "--n-gpu-layers", "99", "--ctx-size", "4096", "--fit", "off",
            "--parallel", "1", "--threads", "4", "--batch-size", "128", "--ubatch-size", "128",
            "--jinja", "--reasoning", "off",
            "--flash-attn", "off", "--no-ui", "--no-agent",
        ]
        if self.lora is not None:
            args.extend(["--lora", str(self.lora)])
        return args

    async def prime(self):
        """Absorb first-inference CUDA setup with synthetic data before readiness."""
        payload = {
            "model": "kitsu-server-chat", "stream": False, "max_tokens": 1,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": "Reply OK. Local startup check. "
                          + "Community server information. " * 128 + " /no_think"}],
        }
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45), trust_env=False) as session:
                async with session.post(self.url + "/v1/chat/completions", json=payload) as response:
                    response.raise_for_status()
                    data = await response.json()
                    return bool(data.get("choices"))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            logger.warning("Server chat inference warm-up failed", exc_info=True)
            return False

    async def start(self):
        if await self.health():
            self.ready = await self.prime()
            return self.ready
        if not self.autostart:
            return False
        if not self.binary.is_file() or not self.model.is_file():
            logger.warning("Server chat model/runtime missing. Run python llm/server_chat/setup_model.py.")
            return False
        self.log = (self.model.parent / "runtime.log").open("ab")
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.arguments(), stdout=self.log, stderr=self.log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            for _ in range(60):
                if self.process.returncode is not None:
                    logger.error("Server chat CUDA runtime exited; inspect models/server-chat/runtime.log.")
                    break
                if await self.health():
                    self.ready = await self.prime()
                    if self.ready:
                        return True
                    break
                await asyncio.sleep(1)
        except (OSError, ValueError):
            logger.exception("Could not start server chat runtime")
        await self.close()
        return False

    async def close(self):
        self.ready = False
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None
