"""FastAPI-приложение: сессии, загрузки, видео и события аналитики, история."""

import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from src.logging_config import setup_logger
from .config import settings
from .routes import sessions as sessions_routes
from .routes import streams as streams_routes
from .routes import uploads as uploads_routes
from .routes.uploads import migrate_local_uploads_in_background
from .routes import events as events_routes
from .routes import history as history_routes
from .routes import recordings as recordings_routes
from .routes import storage as storage_routes
from src.service_health import REQUIRED_BY_MODALITY
from src.service_health import SERVICE_TITLES
from src.service_health import not_ready_reasons
from .schemas import HealthInfo
from .schemas import ServiceHealthInfo
from .services.session_manager import session_manager
from .services.stream_dispatcher import live_dispatcher
from .services.stream_dispatcher import stream_dispatcher
from .services.events_dispatcher import events_dispatcher
from .services import db as db_service
from .services.retention import RetentionService

setup_logger("webserver", json_format=False)
log = logging.getLogger("webserver")

retention = RetentionService(settings.RECORDINGS_RETENTION_DAYS, settings.UPLOADS_RETENTION_DAYS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("Starting web server services")
    db_service.init_pool()
    await run_in_threadpool(session_manager.start)
    migrate_local_uploads_in_background()
    retention.start()
    await stream_dispatcher.start()
    await live_dispatcher.start()
    await events_dispatcher.start()
    try:
        yield
    finally:
        log.info("Shutting down web server services")
        retention.stop()
        session_manager.close()
        await stream_dispatcher.stop()
        await live_dispatcher.stop()
        await events_dispatcher.stop()
        db_service.close_pool()


app = FastAPI(
    title="Affectra API",
    version="0.1.0",
    description="Управление сессиями обработки и стриминг аннотированного видео.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions_routes.router)
app.include_router(uploads_routes.router)
app.include_router(streams_routes.router)
app.include_router(streams_routes.live_router)
app.include_router(events_routes.router)
app.include_router(history_routes.router)
app.include_router(recordings_routes.router)
app.include_router(storage_routes.router)


@app.get("/api/health", response_model=HealthInfo, tags=["meta"])
def health():
    services = session_manager.services()
    info     = HealthInfo(
        status="ok",
        kafka=True,
        sessions=len(session_manager.list()),
        uploads_dir=str(settings.UPLOADS_DIR),
    )
    if services is not None:
        info.services  = {name: ServiceHealthInfo(title=SERVICE_TITLES.get(name, name), ready=h.ready,
                                                  starting=h.starting, stale=h.stale)
                          for name, h in sorted(services.items())}
        info.ready_for = {m: not not_ready_reasons(services, [m]) for m in REQUIRED_BY_MODALITY}
        info.not_ready = not_ready_reasons(services, list(REQUIRED_BY_MODALITY))
    return info


@app.get("/", tags=["meta"])
def root():
    return {
        "name": "Affectra API",
        "docs": "/docs",
        "health": "/api/health",
    }
