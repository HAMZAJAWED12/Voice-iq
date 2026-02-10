from app.services.diarization_service import diarize_audio
from app.utils.logger import logger
import os

if __name__ == "__main__":
    path = os.path.join("data", "sample.wav")  # or your DIALOGUE.wav/mp3 normalized
    segments = diarize_audio(path)
    logger.info(f"Diarization result:\n{segments}")