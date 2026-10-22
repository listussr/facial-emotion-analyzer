"""Распознавание эмоций по голосу и лицу (обучено на CREMA-D): модели, предобработка, слияние."""
from .backends import CLASSES, load_backends  # noqa: F401
from .fusion import METHODS, Fusion  # noqa: F401
from .pipeline import EmotionPipeline  # noqa: F401
