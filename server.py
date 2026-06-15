from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from src.dsp_engine import MotorDSP

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
UPLOADS_DIR = ASSETS_DIR / "uploads"
TEMP_DIR = BASE_DIR / "temp"
PROCESSED_DIR = TEMP_DIR / "processed"

app = FastAPI(title="Rock Machine DSP API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def ensure_runtime_dirs() -> None:
    for directory in (ASSETS_DIR, UPLOADS_DIR, TEMP_DIR, PROCESSED_DIR):
        directory.mkdir(parents=True, exist_ok=True)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/app.js", include_in_schema=False)
def javascript_bundle() -> FileResponse:
    return FileResponse(BASE_DIR / "app.js", media_type="application/javascript")


@app.post("/api/process-track")
async def process_track(
    instrumento: str = Form(...),
    original_bpm: float = Form(...),
    original_key_id: int = Form(...),
    target_bpm: float = Form(...),
    target_key_id: int = Form(...),
    arquivo: UploadFile = File(...),
):
    if arquivo.content_type not in {"audio/mpeg", "audio/wav", "audio/x-wav", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="Formato de arquivo não suportado.")

    request_id = uuid.uuid4().hex
    original_suffix = Path(arquivo.filename or "upload.wav").suffix.lower() or ".wav"
    safe_instrument = "".join(ch for ch in instrumento.lower() if ch.isalnum() or ch in {"-", "_"}) or "track"

    upload_path = UPLOADS_DIR / f"{safe_instrument}-{request_id}{original_suffix}"
    processed_path = PROCESSED_DIR / f"{safe_instrument}-{request_id}.wav"

    with upload_path.open("wb") as destination:
        shutil.copyfileobj(arquivo.file, destination)

    motor = MotorDSP(bpm_global=target_bpm, tom_global_id=target_key_id)
    try:
        await asyncio.to_thread(
            motor.processar_faixa,
            caminho_entrada=upload_path,
            caminho_saida=processed_path,
            bpm_original=original_bpm,
            tom_original_id=original_key_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Falha ao processar áudio: {exc}") from exc
    finally:
        arquivo.file.close()

    return FileResponse(
        path=processed_path,
        media_type="audio/wav",
        filename=processed_path.name,
    )