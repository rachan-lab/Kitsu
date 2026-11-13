"""A bounded local translation worker; never load its ML dependencies in the bot."""

import asyncio
from collections import OrderedDict
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess

from .languages import LANGUAGES


logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]


class TranslationUnavailable(Exception):
    pass


class LocalTranslator:
    def __init__(self):
        self.python = ROOT / "models/server-chat/translation-env/Scripts/python.exe"
        self.worker = ROOT / "llm/server_chat/translation_worker.py"
        self.directory = ROOT / "models/server-chat/translation/int8"
        self.process = None
        self.log = None
        self.ready = False
        self.pending = 0
        self.lock = asyncio.Lock()
        self.cache = OrderedDict()
        self.restart = None
        self.closed = False

    async def start(self):
        if self.closed or self.ready:
            return self.ready
        if not self.python.is_file() or not (self.directory / "model.bin").is_file():
            return False
        try:
            self.log = (self.directory.parent / "worker.log").open("ab")
            self.process = await asyncio.create_subprocess_exec(
                str(self.python), "-u", str(self.worker), str(self.directory),
                cwd=ROOT, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=self.log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), limit=65536,
                env={**{key: value for key, value in os.environ.items() if key.upper() in {
                    "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                    "PROGRAMDATA", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
                }}, "PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1"},
            )
            line = await asyncio.wait_for(self.process.stdout.readline(), 30)
            self.ready = json.loads(line).get("ready") is True
            if self.ready:
                logger.info("Local CPU translator ready: %s selected languages", len(LANGUAGES))
            if not self.ready:
                await self.stop()
        except (OSError, ValueError, asyncio.TimeoutError):
            logger.warning("Local translator could not start", exc_info=True)
            await self.stop()
        return self.ready

    async def close(self):
        self.closed = True
        if self.restart is not None and self.restart is not asyncio.current_task():
            self.restart.cancel()
            await asyncio.gather(self.restart, return_exceptions=True)
            self.restart = None
        await self.stop()

    async def stop(self):
        self.ready = False
        if self.process is not None:
            if self.process.returncode is None:
                if os.name == "nt":
                    # Windows venv Python has a launcher and an interpreter child.
                    helper = await asyncio.create_subprocess_exec(
                        str(Path(os.environ["SYSTEMROOT"]) / "System32/taskkill.exe"),
                        "/PID", str(self.process.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    await helper.wait()
                else:
                    self.process.kill()
            await self.process.wait()
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None

    async def call(self, payload):
        if not self.ready:
            raise TranslationUnavailable("Local translation is getting ready or unavailable. Please try again shortly.")
        if self.pending >= 3:
            raise TranslationUnavailable("I'm translating a few answers. Please try again shortly.")
        self.pending += 1
        try:
            try:
                await asyncio.wait_for(self.lock.acquire(), 1)
            except asyncio.TimeoutError as error:
                raise TranslationUnavailable("I'm translating another answer. Please try again shortly.") from error
            try:
                return await asyncio.wait_for(self.exchange(payload), 12)
            except asyncio.CancelledError:
                await self.close()
                raise
            except (OSError, ValueError, asyncio.TimeoutError) as error:
                # A late response must never serve the next member.
                await self.stop()
                if not self.closed:
                    self.restart = asyncio.create_task(self.start())
                raise TranslationUnavailable("Translation became unavailable. Please try again shortly.") from error
            finally:
                self.lock.release()
        finally:
            self.pending -= 1

    async def exchange(self, payload):
        if not self.ready:
            raise TranslationUnavailable("Local translation is unavailable. Please try again shortly.")
        if self.process is None or self.process.returncode is not None:
            raise OSError("The owned translation worker exited")
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.process.stdin.drain()
        data = json.loads(await self.process.stdout.readline())
        if not isinstance(data, dict):
            raise ValueError("Invalid translator reply")
        if data.get("error"):
            raise TranslationUnavailable("I couldn't translate that reliably. Try a shorter server question.")
        return data

    async def detect(self, text):
        if not self.ready or sum(character.isalpha() for character in text) < 4:
            return "en"
        result = await self.call({"operation": "detect", "text": text[:700]})
        code = result.get("language")
        return code if code in LANGUAGES and result.get("confidence", 0) >= 0.90 else "en"

    async def translate(self, text, target, source="en"):
        if target not in LANGUAGES or source not in LANGUAGES:
            raise TranslationUnavailable("That language isn't selected for server chat.")
        if source == target:
            return text
        if len(text) > 1850:
            raise TranslationUnavailable("Please use a shorter passage to translate.")
        key = (source, target, hashlib.sha256(text.encode("utf-8")).hexdigest())
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        result = await self.call({"operation": "translate", "text": text, "source": source, "target": target})
        translated = result.get("text")
        if not isinstance(translated, str) or not translated.strip() or len(translated) > 1850:
            raise TranslationUnavailable("I couldn't fit a reliable translation. Try a shorter passage.")
        self.cache[key] = translated
        if len(self.cache) > 256:
            self.cache.popitem(last=False)
        return translated
