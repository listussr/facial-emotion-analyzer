from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DetectedFaceModel(object):
    track_id: str
    frame_id: str
    camera_id: str
    bbox: List[float]
    confidence: float
