import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.logging_config import setup_logger

from .config import settings
from .routes import sessions as sessions_routes
from .routes import streams as streams_routes
from .routes import uploads as uploads_routes
from .routes import events as events_routes
from .schemas import HealthInfo
from .services.session_manager import session_manager
from .services.stream_dispatcher import stream_dispatcher
from .services.events_dispatcher import events_dispatcher

setup_logger("webserver", json_format=False)
log = logging.getLogger("webserver")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("Starting web server services")
    await stream_dispatcher.start()
    await events_dispatcher.start()
    try:
        yield
    finally:
        log.info("Shutting down web server services")
        session_manager.stop_all()
        await stream_dispatcher.stop()
        await events_dispatcher.stop()


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
app.include_router(events_routes.router)


@app.get("/api/health", response_model=HealthInfo, tags=["meta"])
def health():
    return HealthInfo(
        status="ok",
        kafka=True,  # TODO: ping реального Kafka
        sessions=len(session_manager.list()),
        uploads_dir=str(settings.UPLOADS_DIR),
    )


@app.get("/", tags=["meta"])
def root():
    return {
        "name": "Affectra API",
        "docs": "/docs",
        "health": "/api/health",
    }
