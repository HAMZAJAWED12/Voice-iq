# app/services/gender_service.py

import torch
from typing import List, Dict
from functools import lru_cache
from app.utils.logger import logger
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

import librosa
import numpy as np


class GenderService:
    """
    Lightweight gender classification based on voice embeddings.

    IMPORTANT:
    - This is NOT perfect gender detection.
    - It uses pitch & spectral features as a heuristic.
    - Works well enough for conversational analytics.
    """

    @staticmethod
    def _estimate_pitch(audio, sr):
        """
        Simple pitch estimation using librosa.
        Returns normalized f0.
        """
        try:
            f0, _, _ = librosa.pyin(
                audio,
                fmin=80,
                fmax=350,
                sr=sr
            )
            f0 = f0[~np.isnan(f0)]
            if len(f0) == 0:
                return None
            return float(np.mean(f0))
        except Exception as e:
            logger.error(f"Pitch estimation failed: {e}")
            return None

    @classmethod
    def infer_gender_from_audio(cls, wav_path: str, segment: Dict) -> Dict:
        """
        Given the entire wav file + one diarization segment, crop that audio
        and estimate gender from the pitch profile.
        """

        try:
            audio, sr = librosa.load(wav_path, sr=16000, mono=True)

            start = int(segment["start"] * sr)
            end = int(segment["end"] * sr)
            chunk = audio[start:end]

            if len(chunk) < sr * 0.3:
                return {
                    "gender": "unknown",
                    "confidence": 0.0
                }

            pitch = cls._estimate_pitch(chunk, sr)

            if pitch is None:
                return {
                    "gender": "unknown",
                    "confidence": 0.0
                }

            # VERY ROUGH heuristic thresholds
            if pitch < 145:
                return {"gender": "male", "confidence": 0.85}
            elif pitch > 185:
                return {"gender": "female", "confidence": 0.85}
            else:
                # ambiguous zone
                return {"gender": "unknown", "confidence": 0.40}

        except Exception as e:
            logger.error(f"Gender inference failed: {e}")
            return {
                "gender": "unknown",
                "confidence": 0.0
            }

    @classmethod
    def add_gender_to_segments(cls, speaker_segments: List[Dict], wav_path: str) -> List[Dict]:
        """
        Loop through speaker segments and attach gender prediction.
        """

        enriched = []

        for seg in speaker_segments:
            result = cls.infer_gender_from_audio(wav_path, seg)
            seg["gender"] = result["gender"]
            seg["gender_confidence"] = result["confidence"]
            enriched.append(seg)

        return enriched