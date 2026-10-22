"""Слияние модальностей: vision-analytics и audio-analytics -> fused-analytics."""

from .fuse import DEFAULT_METHOD
from .fuse import METHODS
from .fuse import FusedSmoother
from .fuse import FusionParams
from .fuse import ModalityFusion
from .voice_state import VoiceEvidence
from .voice_state import VoiceState
from .voice_state import overlap_ms

__all__ = ["DEFAULT_METHOD", "METHODS", "FusedSmoother", "FusionParams", "ModalityFusion",
           "VoiceEvidence", "VoiceState", "overlap_ms"]
