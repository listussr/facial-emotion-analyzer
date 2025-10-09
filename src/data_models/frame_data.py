from dataclasses import dataclass


@dataclass
class FrameModel(object):
    frame_id: str
    camera_id: str
    timestamp: float
    frame_data: bytes
    width: int
    height: int
