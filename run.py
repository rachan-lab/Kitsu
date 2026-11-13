import logging
import sys
import threading
from pathlib import Path

from waitress import serve

from app.api.app import app
from app.bot.client import Kitsu
from app.bot.instance import BotAlreadyRunning, bot_instance
from app.config import settings


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def run_web_server():
    # app.run(
    #     host=settings.flask_host,
    #     port=settings.flask_port,
    # )
    serve(
        app,
        host=settings.flask_host,
        port=settings.flask_port,
        threads=4,
    )


def main():
    try:
        with bot_instance(Path(__file__).resolve().parent / "data/runtime/kitsu.lock"):
            web_thread = threading.Thread(
                target=run_web_server,
                name="kitsu-web-server",
                daemon=True,
            )
            web_thread.start()
            bot = Kitsu()
            bot.run(settings.discord_token)
    except BotAlreadyRunning as error:
        logging.getLogger(__name__).error("%s", error)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
