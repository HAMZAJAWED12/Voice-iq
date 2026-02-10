# app/main.py
from fastapi import FastAPI
from app.routes.process_audio import router as process_router
from app.utils.logger import setup_logging
from dotenv import load_dotenv

load_dotenv()
setup_logging()
app = FastAPI(title="voiceiq-ai", version="voiceiq-ai/0.1.0")
app.include_router(process_router, prefix="/v1")

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/version")
def version():
    return {"version": app.version}