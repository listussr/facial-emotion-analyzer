"""Запуск веб-сервера; продюсеры сессий работают в этом же процессе."""

import uvicorn

from src.cpu_threads import limit_opencv_threads

if __name__ == "__main__":
    limit_opencv_threads()
    uvicorn.run(
        "src.web_server.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
