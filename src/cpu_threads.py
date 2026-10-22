"""Ограничение потоков OpenCV и приоритета процессов, чтобы сервисы не отнимали CPU у видеопайплайна."""

import logging
import os


def limit_opencv_threads(default: int = 1) -> int:
    """
    Число потоков OpenCV из OPENCV_NUM_THREADS.

    Лишние потоки почти не ускоряют YuNet, resize и JPEG, но сжигают в разы больше CPU.

    Args:
        default (int, optional): Значение без переменной окружения, 0 — на усмотрение OpenCV. Defaults to 1.

    Returns:
        int: Выставленное число потоков.
    """
    import cv2

    n = int(os.getenv("OPENCV_NUM_THREADS", str(default)))
    if n > 0:
        cv2.setNumThreads(n)
    logging.info(f"OpenCV threads: {cv2.getNumThreads()}")
    return n


def lower_priority(default: str = "low") -> str:
    """
    Понижение приоритета фонового сервиса, чтобы видеопайплайн получал CPU первым.

    Args:
        default (str, optional): Уровень без PROCESS_PRIORITY: "low" или "normal". Defaults to "low".

    Returns:
        str: Применённый уровень.
    """
    level = os.getenv("PROCESS_PRIORITY", default).strip().lower()
    if level != "low":
        return level
    try:
        import psutil
        p = psutil.Process()
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 10)
    except ImportError:
        if hasattr(os, "nice"):
            os.nice(10)
    except Exception:
        logging.exception("Cannot lower process priority")
        return "normal"
    logging.info("Process priority lowered (background service)")
    return level
