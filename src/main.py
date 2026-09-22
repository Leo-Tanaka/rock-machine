
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import shutil

from src.database import musicas_collection

app = FastAPI(
    title="Rock Machine API",
    description="API para a plataforma Rock Machine",
    version="1.0.0"
)

# Caminho da raiz do projeto
BASE_DIR = Path(__file__).resolve().parent.parent

# Pasta onde os áudios serão armazenados
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Disponibiliza os arquivos da pasta uploads por URL
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.get("/")
def inicio():
    return {"mensagem": "Rock Machine API está funcionando!"}


@app.get("/publicar", include_in_schema=False)
def pagina_publicar():
    """Exibe o formulário temporário para cadastrar um WAV na biblioteca."""
    return FileResponse(BASE_DIR / "publicar.html")


# Listar músicas
@app.get("/musicas")
def listar_musicas():
    musicas = list(musicas_collection.find())

    for musica in musicas:
        musica["_id"] = str(musica["_id"])

    return musicas


# Upload de uma música
@app.post("/musicas/upload", status_code=201)
def upload_musica(
    nome: str = Form(...),
    autor: str = Form(...),
    arquivo: UploadFile = File(...)
):
    # Aceita apenas arquivos WAV
    if Path(arquivo.filename or "").suffix.lower() != ".wav":
        raise HTTPException(
            status_code=400,
            detail="Envie um arquivo WAV."
        )

    # Gera um nome único para o arquivo
    nome_arquivo = f"{uuid4().hex}.wav"
    caminho_arquivo = UPLOAD_DIR / nome_arquivo

    try:
        # Salva o arquivo enviado na pasta uploads
        with caminho_arquivo.open("wb") as destino:
            shutil.copyfileobj(arquivo.file, destino)

        # Cria o documento que será salvo no MongoDB
        nova_musica = {
            "nome": nome,
            "autor": autor,
            "arquivo": nome_arquivo,
            "url_audio": f"/uploads/{nome_arquivo}",
            "data_criacao": datetime.now(timezone.utc)
        }

        resultado = musicas_collection.insert_one(nova_musica)

        return {
            "mensagem": "Música enviada com sucesso!",
            "id": str(resultado.inserted_id),
            "nome": nome,
            "autor": autor,
            "url_audio": nova_musica["url_audio"]
        }

    except Exception:
        # Se o cadastro falhar, remove o arquivo salvo
        caminho_arquivo.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail="Não foi possível salvar a música."
        )

    finally:
        arquivo.file.close()
