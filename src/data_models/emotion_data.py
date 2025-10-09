from dataclasses import dataclass


@dataclass
class EmotionModel(object):
    track_id: str
    frame_id: str
    emotion: str
    confidence: float
    timestamp: float
