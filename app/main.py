import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.bot.telegram_bot import build_application, start_polling, stop_polling
from app.config import get_settings
from app.database import async_session, init_db
from app.routers.admin import router as admin_router
from app.routers.health import router as health_router

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_db()

    telegram_app = None
    polling_task = None
    if settings.bot_polling and settings.telegram_bot_token:
        telegram_app = build_application(settings, async_session)
        polling_task = asyncio.create_task(start_polling(telegram_app))
    elif settings.bot_polling:
        logger.warning("BOT_POLLING is true, but TELEGRAM_BOT_TOKEN is not configured")

    app.state.telegram_app = telegram_app
    try:
        yield
    finally:
        if telegram_app is not None:
            await stop_polling(telegram_app)
        if polling_task is not None:
            polling_task.cancel()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Arabic Turkish Translation Bot", lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(health_router)
    app.include_router(admin_router)
    return app


app = create_app()
