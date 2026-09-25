# uvicorn src.main:app --reload
import os
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from supabase import create_client, Client

from src.database import musicas_collection
from dotenv import load_dotenv

app = FastAPI(
    title="Rock Machine API",
    description="API para a plataforma Rock Machine",
    version="1.0.0"
)

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
IMAGES_DIR = BASE_DIR / "images"

# Configuração do Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_BUCKET = os.getenv(
    "SUPABASE_BUCKET",
    "rock-machine_audios"
)

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError(
        "SUPABASE_URL ou SUPABASE_KEY não encontrada no .env"
    )

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

# Imagens continuam sendo servidas localmente por enquanto
if IMAGES_DIR.exists():
    app.mount(
        "/images",
        StaticFiles(directory=IMAGES_DIR),
        name="images"
    )


@app.get("/")
def inicio():
    return FileResponse(BASE_DIR / "index.html")


@app.get("/studio", include_in_schema=False)
def studio():
    return FileResponse(BASE_DIR / "inicio.html")


@app.get("/app.js", include_in_schema=False)
def javascript_do_studio():
    return FileResponse(
        BASE_DIR / "app.js",
        media_type="application/javascript"
    )


@app.get("/publicar", include_in_schema=False)
def pagina_publicar():
    return FileResponse(BASE_DIR / "publicar.html")


@app.get("/playlist", include_in_schema=False)
def pagina_playlist():
    return FileResponse(BASE_DIR / "playlist.html")


# Listar músicas
@app.get("/musicas")
def listar_musicas():
    musicas = list(
        musicas_collection.find().sort("data_criacao", -1)
    )

    for musica in musicas:
        musica["_id"] = str(musica["_id"])

    return musicas


# Upload de uma música para o Supabase
@app.post("/musicas/upload", status_code=201)
def upload_musica(
    nome: str = Form(...),
    autor: str = Form(...),
    arquivo: UploadFile = File(...)
):
    if Path(arquivo.filename or "").suffix.lower() != ".wav":
        raise HTTPException(
            status_code=400,
            detail="Envie um arquivo WAV."
        )

    nome_arquivo = f"{uuid4().hex}.wav"
    caminho_storage = nome_arquivo

    try:
        # Lê o WAV enviado pelo formulário
        conteudo = arquivo.file.read()

        if not conteudo:
            raise HTTPException(
                status_code=400,
                detail="O arquivo WAV está vazio."
            )

        # Envia o arquivo para o Supabase Storage
        supabase.storage.from_(
            SUPABASE_BUCKET
        ).upload(
            path=caminho_storage,
            file=conteudo,
            file_options={
                "content-type": "audio/wav",
                "upsert": "false"
            }
        )

        # Gera a URL pública do arquivo
        url_audio = supabase.storage.from_(
            SUPABASE_BUCKET
        ).get_public_url(caminho_storage)

        # Salva os metadados no MongoDB Atlas
        nova_musica = {
            "nome": nome,
            "autor": autor,
            "arquivo": nome_arquivo,
            "url_audio": url_audio,
            "data_criacao": datetime.now(timezone.utc)
        }

        resultado = musicas_collection.insert_one(nova_musica)

        return {
            "mensagem": "Música enviada com sucesso!",
            "id": str(resultado.inserted_id),
            "nome": nome,
            "autor": autor,
            "url_audio": url_audio
        }

    except HTTPException:
        raise

    except Exception as erro:
        print(f"Erro ao publicar música: {erro}")

        # Se o upload no Storage ocorreu, mas o cadastro
        # no MongoDB falhou, tenta remover o WAV do Storage.
        try:
            supabase.storage.from_(
                SUPABASE_BUCKET
            ).remove([caminho_storage])
        except Exception as erro_storage:
            print(f"Erro ao remover WAV: {erro_storage}")

        raise HTTPException(
            status_code=500,
            detail="Não foi possível salvar a música."
        )

    finally:
        arquivo.file.close()