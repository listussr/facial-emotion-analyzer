"""
Аудиомодальность: распознавание эмоций по голосу из топика raw-audio-chunks.
"""
from .contracts import AudioAnalytics, RESULT_SCHEMA_VERSION
from .buffer import AudioWindow, CameraAudioBuffer
from .vad import EnergyVAD
from .recognizer import STUB_MODEL, VoiceEmotionRecognizer

__all__ = [
    'AudioAnalytics', 'RESULT_SCHEMA_VERSION',
    'AudioWindow', 'CameraAudioBuffer',
    'EnergyVAD',
    'STUB_MODEL', 'VoiceEmotionRecognizer',
]
