# uvicorn src.main:app --reload
import os
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
import requests

import json
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from supabase import create_client, Client
from fastapi.middleware.cors import CORSMiddleware



from src.database import musicas_collection
from dotenv import load_dotenv

# URL do backend público
URL_BACKEND_PUBLICO = os.getenv(
    "URL_BACKEND_PUBLICO",
    "https://rock-machine.onrender.com"
).rstrip("/")

# O Render define essa variável automaticamente
ESTA_NO_RENDER = os.getenv("RENDER") == "true"

app = FastAPI(
    title="Rock Machine API",
    description="API para a plataforma Rock Machine",
    version="1.0.0"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
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


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(IMAGES_DIR / "logo-rock-machine.png")


@app.get("/")
def inicio():
    return FileResponse(BASE_DIR / "frontend/studio.html")


@app.get("/studio", include_in_schema=False)
def studio():
    return FileResponse(BASE_DIR / "frontend/studio.html")


@app.get("/mixer", include_in_schema=False)
def mixer_stems():
    return FileResponse(BASE_DIR / "frontend/index.html")


@app.get("/app.js", include_in_schema=False)
def javascript_do_studio():
    return FileResponse(
        BASE_DIR / "frontend/js/app.js",
        media_type="application/javascript"
    )


@app.get("/publicar", include_in_schema=False)
def pagina_publicar():
    return FileResponse(BASE_DIR / "frontend/publicar.html")


@app.get("/publicar_manual", include_in_schema=False)
def pagina_publicar_manual():
    return FileResponse(BASE_DIR / "frontend/publicar_manual.html")


@app.get("/playlist", include_in_schema=False)
def pagina_playlist():
    return FileResponse(BASE_DIR / "frontend/playlist.html")


# Listar músicas
@app.get("/musicas")
def listar_musicas():
    # Consulta diretamente o MongoDB Atlas (funciona tanto local quanto no Render)
    try:
        musicas = list(
            musicas_collection.find().sort("data_criacao", -1)
        )

        for musica in musicas:
            musica["_id"] = str(musica["_id"])

        return musicas

    except Exception as erro:
        print(f"Erro ao consultar o MongoDB: {erro}")

        # Fallback opcional: se o acesso direto falhar e não estiver no Render, tenta consultar o Render
        if not ESTA_NO_RENDER:
            try:
                print("Tentando fallback via backend público no Render...")
                resposta = requests.get(
                    f"{URL_BACKEND_PUBLICO}/musicas",
                    timeout=10
                )
                resposta.raise_for_status()
                return resposta.json()
            except requests.RequestException as erro_render:
                print(f"Fallback via backend público também falhou: {erro_render}")

        raise HTTPException(
            status_code=500,
            detail="Não foi possível carregar as músicas."
        )



# Upload de uma música para o Supabase
@app.post("/musicas/upload", status_code=201)
async def upload_musica(
    nome: str = Form(...),
    autor: str = Form(...),
    arquivo: UploadFile = File(...),
    camadas: str = Form("[]"),
):

    try:
        camadas_lista = json.loads(camadas)
        print("CAMADAS RECEBIDAS:", repr(camadas))
        print("CAMADAS CONVERTIDAS:", camadas_lista)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Formato inválido para as camadas."
        )

    if not isinstance(camadas_lista, list):
        raise HTTPException(
            status_code=400,
            detail="As camadas devem ser uma lista."
        )
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
            "camadas": camadas_lista, 
            "data_criacao": datetime.now(timezone.utc)
        }

        resultado = musicas_collection.insert_one(nova_musica)
        print("ID DA MÚSICA SALVA:", resultado.inserted_id)
        print("CAMADAS SALVAS:", nova_musica["camadas"])

        return {
            "mensagem": "Música enviada com sucesso!",
            "id": str(resultado.inserted_id),
            "nome": nome,
            "autor": autor,
            "url_audio": url_audio,
            "camadas": camadas_lista,
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