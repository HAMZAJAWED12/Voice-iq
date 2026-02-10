import os
from huggingface_hub import login
from pyannote.audio import Pipeline

# make sure your .env is loaded or set manually here
token = os.getenv("PYANNOTE_AUTH_TOKEN")
if not token:
    raise ValueError("PYANNOTE_AUTH_TOKEN not found — check your .env or environment variables!")

print("Logging in to Hugging Face...")
login(token=token)

print("Loading pipeline...")
pipeline = Pipeline.from_pretrained(
    "pyannote/speaker-diarization-3.1",
    use_auth_token=token
)
print("Loaded pipeline successfully!")
