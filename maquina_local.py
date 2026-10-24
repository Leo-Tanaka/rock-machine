"""
RockMachine - Servidor local da máquina física

Responsável por:
- controlar a sessão de criação da música;
- iniciar e finalizar a gravação;
- disponibilizar o estado atual para a interface web;
- receber eventos do Arduino/RFID;
- permitir simulação enquanto o hardware não está conectado;
- enviar a música finalizada para o backend público.

O áudio continua sendo responsabilidade do RockMachinePlayer.
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
import requests
import uvicorn

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from rockmachine_motor import (
    RockMachinePlayer,
    BPM_ALVO,
    COMPASSOS_POR_LOOP,
    TEMPOS_POR_COMPASSO,
    PASTA_STEMS,
)

from ponte_serial import (
    UID_PARA_MUSICA,
    UID_PARA_EFEITO,
)


# =========================================================
# CONFIGURAÇÕES
# =========================================================

HOST = "127.0.0.1"
PORTA = 8001

PASTA_GRAVACOES = Path("gravacoes")
PASTA_GRAVACOES.mkdir(exist_ok=True)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# URLs dos backends (local para desenvolvimento/testes, público para produção)
URL_BACKEND_PUBLICO = os.getenv(
    "URL_BACKEND_PUBLICO",
    "https://rock-machine.onrender.com"
).rstrip("/")

URL_BACKEND_LOCAL = os.getenv(
    "URL_BACKEND_LOCAL",
    "http://127.0.0.1:8000"
).rstrip("/")


def resolver_url_backend() -> str:
    """
    Retorna a URL do backend para envio da música:
    1. Se URL_BACKEND estiver definido no .env, tem prioridade total.
    2. Se o backend local (porta 8000) estiver respondendo, usa o local
       (elimina a espera de 120s e timeouts do Render durante testes locais).
    3. Caso contrário, envia para o backend público hospedado no Render.
    """
    url_env = os.getenv("URL_BACKEND")
    if url_env:
        return url_env.rstrip("/")

    try:
        r = requests.get(f"{URL_BACKEND_LOCAL}/health", timeout=1.0)
        if r.status_code == 200:
            return URL_BACKEND_LOCAL
    except Exception:
        pass

    return URL_BACKEND_PUBLICO


# =========================================================
# FRONTEND
# =========================================================

FRONTEND_DIR = (
    Path(__file__).resolve().parent / "frontend"
)


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="Rock Machine - Máquina Local",
    description="Controle local da máquina física Rock Machine.",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# PÁGINAS
# =========================================================

@app.get("/maquina")
def pagina_maquina():

    return FileResponse(
        FRONTEND_DIR / "maquina.html"
    )


@app.get("/publicar")
def pagina_publicar():

    return FileResponse(
        FRONTEND_DIR / "publicar.html"
    )


# =========================================================
# ESTADO DA SESSÃO
# =========================================================

estado_sessao = {
    "gravando": False,
    "inicio": None,
    "tempo": 0,
    "camadas": [],
    "arquivo_wav": None,
    "sessao_id": None,
    "publicada": False,
}


# Lock para evitar duas requisições
# alterando a sessão ao mesmo tempo.

lock = threading.Lock()


# =========================================================
# PLAYER
# =========================================================

player = RockMachinePlayer(
    PASTA_STEMS,
    BPM_ALVO,
    COMPASSOS_POR_LOOP,
    TEMPOS_POR_COMPASSO,
)


# =========================================================
# MODELOS
# =========================================================

class SimulacaoAdicionar(BaseModel):
    uid: str
    posicao: int


class SimulacaoRemover(BaseModel):
    posicao: int


class DadosPublicacao(BaseModel):
    nome: str
    autor: str


# =========================================================
# FUNÇÕES AUXILIARES
# =========================================================

def atualizar_tempo():
    """
    Calcula o tempo atual da gravação.
    """

    if (
        estado_sessao["gravando"]
        and estado_sessao["inicio"]
    ):

        estado_sessao["tempo"] = (
            time.time()
            - estado_sessao["inicio"]
        )

    # IMPORTANTE:
    # Quando a gravação já terminou, não zeramos
    # o tempo. Assim a página de publicação pode
    # continuar mostrando a duração final.
    else:
        pass


def registrar_camada(
    uid,
    posicao,
):
    """
    Adiciona uma camada à sessão atual.

    Esta função é usada pela simulação agora
    e posteriormente poderá ser chamada pelos
    eventos reais do Arduino.
    """

    dados = UID_PARA_MUSICA.get(uid)

    if not dados:
        return None

    if isinstance(dados, dict):
        musica_nome = dados.get("musica", "")
        artista_nome = dados.get("artista", "")
        tipo_nome = dados.get("tipo", "")
    else:
        musica_nome = str(dados)
        artista_nome = ""
        tipo_nome = {0: "drums", 1: "other", 2: "vocals"}.get(posicao, "")

    camada = {
        "uid": uid,
        "posicao": posicao,
        "musica": musica_nome,
        "artista": artista_nome,
        "tipo": tipo_nome,
        "ativa": True,
    }

    # Se já existe uma camada naquela posição,
    # substituímos pela nova.
    estado_sessao["camadas"] = [
        camada_atual
        for camada_atual in estado_sessao["camadas"]
        if camada_atual["posicao"] != posicao
    ]

    estado_sessao["camadas"].append(camada)

    return camada


def remover_camada(posicao):
    """
    Remove a camada associada à posição.
    """

    estado_sessao["camadas"] = [
        camada
        for camada in estado_sessao["camadas"]
        if camada["posicao"] != posicao
    ]


# =========================================================
# STATUS
# =========================================================

@app.get("/sessao/status")
def obter_status():

    with lock:

        atualizar_tempo()

        return {
            "gravando": estado_sessao["gravando"],
            "tempo": round(
                estado_sessao["tempo"],
                1
            ),
            "camadas": estado_sessao["camadas"],
            "arquivo_wav": estado_sessao["arquivo_wav"],
            "sessao_id": estado_sessao["sessao_id"],
            "publicada": estado_sessao["publicada"],
        }


# =========================================================
# INICIAR SESSÃO
# =========================================================

@app.post("/sessao/iniciar")
def iniciar_sessao():

    with lock:

        if estado_sessao["gravando"]:

            raise HTTPException(
                status_code=400,
                detail="Já existe uma sessão em andamento."
            )

        # Limpa o estado anterior.

        estado_sessao["gravando"] = True
        estado_sessao["inicio"] = time.time()
        estado_sessao["tempo"] = 0
        estado_sessao["camadas"] = []
        estado_sessao["arquivo_wav"] = None
        estado_sessao["sessao_id"] = str(
            uuid.uuid4()
        )
        estado_sessao["publicada"] = False

        # O próprio motor começa a registrar
        # os eventos musicais.

        player.iniciar_gravacao()

        return {
            "sucesso": True,
            "sessao_id": estado_sessao["sessao_id"],
        }


# =========================================================
# FINALIZAR SESSÃO
# =========================================================

@app.post("/sessao/finalizar")
def finalizar_sessao():

    with lock:

        if not estado_sessao["gravando"]:

            raise HTTPException(
                status_code=400,
                detail="Não existe uma sessão sendo gravada."
            )

        atualizar_tempo()

        sessao_id = estado_sessao["sessao_id"]

        nome_arquivo = (
            f"rockmachine_{sessao_id}.wav"
        )

        caminho_wav = (
            PASTA_GRAVACOES / nome_arquivo
        )

        duracao = estado_sessao["tempo"]

        if duracao <= 0:
            duracao = 1

        # ---------------------------------------------
        # 1. GERA E SALVA O WAV
        # ---------------------------------------------

        player.parar_gravacao_e_salvar(
            str(caminho_wav),
            duracao,
        )

        # ---------------------------------------------
        # 2. PARA O ÁUDIO AO VIVO
        # ---------------------------------------------

        player.parar_todos_os_sons()

        # ---------------------------------------------
        # 3. ATUALIZA O ESTADO DA SESSÃO
        # ---------------------------------------------

        estado_sessao["gravando"] = False
        estado_sessao["inicio"] = None
        estado_sessao["tempo"] = duracao
        estado_sessao["arquivo_wav"] = str(
            caminho_wav
        )

        return {
            "sucesso": True,
            "arquivo_wav": str(caminho_wav),
            "tempo": round(duracao, 1),
            "camadas": estado_sessao["camadas"],
        }


# =========================================================
# SESSÃO FINALIZADA
# =========================================================

@app.get("/sessao/finalizada")
def obter_sessao_finalizada():

    with lock:

        caminho_wav = estado_sessao["arquivo_wav"]

        if not caminho_wav:

            raise HTTPException(
                status_code=404,
                detail="Nenhuma sessão finalizada encontrada."
            )

        caminho = Path(caminho_wav)

        if not caminho.exists():

            raise HTTPException(
                status_code=404,
                detail="O arquivo WAV da sessão não foi encontrado."
            )

        return {
            "arquivo_wav": str(caminho),
            "tempo": round(
                estado_sessao["tempo"],
                1
            ),
            "camadas": estado_sessao["camadas"],
            "sessao_id": estado_sessao["sessao_id"],
            "publicada": estado_sessao["publicada"],
        }


# =========================================================
# PUBLICAR SESSÃO
# =========================================================

@app.post("/sessao/publicar")
def publicar_sessao(
    dados: DadosPublicacao
):

    nome = dados.nome.strip()
    autor = dados.autor.strip()

    if not nome:

        raise HTTPException(
            status_code=400,
            detail="Informe o nome da música."
        )

    if not autor:

        raise HTTPException(
            status_code=400,
            detail="Informe o nome do autor."
        )


    # -----------------------------------------------------
    # Pega os dados da sessão
    # -----------------------------------------------------

    with lock:

        caminho_wav = estado_sessao["arquivo_wav"]

        camadas = list(
            estado_sessao["camadas"]
        )

        publicada = estado_sessao["publicada"]


    if not caminho_wav:

        raise HTTPException(
            status_code=400,
            detail="Nenhuma música foi finalizada."
        )


    if publicada:

        raise HTTPException(
            status_code=400,
            detail="Esta sessão já foi publicada."
        )


    caminho = Path(caminho_wav)


    if not caminho.exists():

        raise HTTPException(
            status_code=404,
            detail=(
                "O arquivo WAV não foi encontrado: "
                f"{caminho}"
            )
        )


    # -----------------------------------------------------
    # Envia para o backend (local ou público)
    # -----------------------------------------------------

    backend_base = resolver_url_backend()
    url = f"{backend_base}/musicas/upload"
    print(f"[PUBLICAÇÃO] Enviando música para: {url}")

    try:

        with open(caminho, "rb") as arquivo:

            files = {
                "arquivo": (
                    caminho.name,
                    arquivo,
                    "audio/wav",
                )
            }

            data = {
                "nome": nome,
                "autor": autor,
                "camadas": json.dumps(
                    camadas,
                    ensure_ascii=False,
                ),
            }

            resposta = requests.post(
                url,
                data=data,
                files=files,
                timeout=120,
            )

    except requests.exceptions.RequestException as erro:

        print(f"[ERRO DE CONEXÃO] Falha ao enviar para {url}: {erro}")
        raise HTTPException(
            status_code=502,
            detail=(
                f"Não foi possível conectar ao backend ({backend_base}): "
                f"{erro}"
            ),
        )


    # -----------------------------------------------------
    # Trata resposta do backend
    # -----------------------------------------------------

    if not resposta.ok:

        try:
            detalhe = resposta.json().get(
                "detail",
                "Erro desconhecido no backend.",
            )

        except Exception:

            detalhe = resposta.text

        raise HTTPException(
            status_code=502,
            detail=(
                "O backend recusou a publicação: "
                f"{detalhe}"
            ),
        )


    try:

        resultado = resposta.json()

    except Exception:

        resultado = {
            "mensagem": resposta.text
        }


    # -----------------------------------------------------
    # Marca a sessão como publicada
    # -----------------------------------------------------

    with lock:

        estado_sessao["publicada"] = True


    return {
        "sucesso": True,
        "mensagem": "Música publicada com sucesso!",
        "musica": resultado,
    }


# =========================================================
# CANCELAR SESSÃO
# =========================================================

@app.post("/sessao/cancelar")
def cancelar_sessao():

    with lock:

        if not estado_sessao["gravando"]:

            raise HTTPException(
                status_code=400,
                detail="Não existe uma sessão em andamento."
            )

        # Para o registro de eventos sem gerar publicação.

        player._gravando = False
        player._eventos_gravacao = []

        player.parar_todos_os_sons()

        estado_sessao["gravando"] = False
        estado_sessao["inicio"] = None
        estado_sessao["tempo"] = 0
        estado_sessao["camadas"] = []
        estado_sessao["arquivo_wav"] = None
        estado_sessao["sessao_id"] = None
        estado_sessao["publicada"] = False

        return {
            "sucesso": True,
            "mensagem": "Sessão cancelada.",
        }


# =========================================================
# SIMULAÇÃO — ADICIONAR RFID
# =========================================================

@app.post("/simulacao/adicionar")
def simulacao_adicionar(
    dados: SimulacaoAdicionar
):

    with lock:

        if not estado_sessao["gravando"]:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Inicie uma sessão antes "
                    "de adicionar uma camada."
                )
            )

        uid = dados.uid.upper()

        musica = UID_PARA_MUSICA.get(uid)

        if not musica:

            raise HTTPException(
                status_code=404,
                detail=f"UID não encontrado: {uid}"
            )

        posicao = dados.posicao

        instrumento = {
            0: "drums",
            1: "vocals",
            2: "other",
        }.get(posicao)

        if instrumento is None:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Posição inválida. "
                    "Use 0, 1 ou 2."
                )
            )

        # Simula exatamente a ação que o RFID
        # faria através da ponte serial.

        nome_musica = musica.get("musica") if isinstance(musica, dict) else str(musica)
        player.colocar_ficha(
            posicao,
            nome_musica,
            instrumento,
        )

        camada = registrar_camada(
            uid,
            posicao,
        )

        return {
            "sucesso": True,
            "camada": camada,
        }


# =========================================================
# SIMULAÇÃO — REMOVER RFID
# =========================================================

@app.post("/simulacao/remover")
def simulacao_remover(
    dados: SimulacaoRemover
):

    with lock:

        posicao = dados.posicao

        player.retirar_ficha(posicao)

        remover_camada(posicao)

        return {
            "sucesso": True,
            "posicao": posicao,
        }


# =========================================================
# EXECUÇÃO
# =========================================================

if __name__ == "__main__":

    print()
    print("==========================================")
    print("       ROCK MACHINE - MÁQUINA LOCAL")
    print("==========================================")
    print()
    print(
        f"Servidor: http://{HOST}:{PORTA}"
    )
    print(
        f"Máquina:  http://{HOST}:{PORTA}/maquina"
    )
    print(
        f"Publicar: http://{HOST}:{PORTA}/publicar"
    )
    print()
    print("Ctrl+C para encerrar.")
    print()

    with player.iniciar_stream():

        uvicorn.run(
            app,
            host=HOST,
            port=PORTA,
        )