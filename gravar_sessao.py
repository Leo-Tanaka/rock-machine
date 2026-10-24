"""
Rock Machine - Servidor da Máquina Física & Sessão de Gravação
====================================================================
Controla a aplicação física (Arduinos + Leitores RFID MFRC522)
com suporte híbrido a seleção manual de faixas via interface web
e publicação direta na playlist pública.

Recursos:
- Escuta as portas seriais dos Arduinos em background (múltiplos leitores, reconexão automática);
- Atualiza o estado da sessão (camadas, tempo, gravação) em tempo real;
- Reproduz os stems de áudio ao vivo sincronizados via RockMachinePlayer;
- Interface web local (http://127.0.0.1:8001/maquina);
- Renderiza o áudio final em WAV ao finalizar;
- Envia diretamente para o backend público/local (/publicar) para alimentar a playlist;
- Fornece funções utilitárias para player_hibrido.py e teste_publicar.py.
"""

from __future__ import annotations

import json
import os
import queue
import sys
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
    calcular_duracao_loop_segundos,
)

from ponte_serial import (
    LeitoresSerial,
    PORTAS_SERIAIS,
    PORTA_SERIAL,
    BAUD_RATE,
    INSTRUMENTO_POR_POSICAO,
    UID_PARA_MUSICA,
    UID_PARA_EFEITO,
)

# =========================================================
# CONFIGURAÇÕES E DIRETÓRIOS
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

HOST_LOCAL = os.getenv("HOST_LOCAL", "127.0.0.1")
PORTA_LOCAL = int(os.getenv("PORTA_LOCAL", 8001))

PASTA_GRAVACOES = BASE_DIR / "gravacoes"
PASTA_GRAVACOES.mkdir(exist_ok=True)

FRONTEND_DIR = BASE_DIR / "frontend"

# 64 compassos = aprox. 2 minutos a 117 BPM (limite máximo de gravação)
COMPASSOS_GRAVACAO = 64
DURACAO_MAXIMA_GRAVACAO = calcular_duracao_loop_segundos(
    BPM_ALVO,
    COMPASSOS_GRAVACAO,
    TEMPOS_POR_COMPASSO,
)

URL_BACKEND_PUBLICO = os.getenv(
    "URL_BACKEND_PUBLICO",
    "https://rock-machine.onrender.com"
).rstrip("/")

URL_BACKEND_LOCAL = os.getenv(
    "URL_BACKEND_LOCAL",
    "http://127.0.0.1:8000"
).rstrip("/")

# Mapa de artistas para enriquecer a exibição visual na interface
ARTISTA_POR_MUSICA = {
    "Billie Jean": "Michael Jackson",
    "Numb": "Linkin Park",
    "Paparazzi": "Lady Gaga",
    "Tudo Que Ela Gosta De Escutar": "Charlie Brown Jr.",
    "Get Lucky": "Daft Punk",
    "Another One Bites the Dust": "Queen",
    "Tempos Modernos": "Lulu Santos",
    "Smells Like Teen Spirit": "Nirvana",
    "Payphone": "Maroon 5",
    "In the End": "Linkin Park",
    "Blinding Lights": "The Weeknd",
    "Blinding Ligths": "The Weeknd",
    "Espresso": "Sabrina Carpenter",
    "Feel Good Inc": "Gorillaz",
    "Levitating": "Dua Lipa",
}


def resolver_url_backend() -> str:
    """
    Retorna a URL do backend para envio da música:
    1. Se URL_BACKEND estiver definido no .env, tem prioridade total.
    2. Se o backend local (porta 8000) estiver respondendo, usa o local.
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
# FUNÇÕES UTILITÁRIAS EXPORTADAS (CLI / PLAYER HÍBRIDO)
# =========================================================

def pedir_dados_publicacao() -> tuple[str, str]:
    """Solicita via terminal o nome da música e do autor."""
    print()
    nome = input("Nome da música: ").strip() or time.strftime("Sessao %Y-%m-%d %H:%M")
    autor = input("Nome do autor: ").strip() or "RockMachine"
    return nome, autor


def nome_para_arquivo(nome_musica: str) -> str:
    """Transforma o nome da música num nome de arquivo seguro."""
    seguro = "".join(c if c.isalnum() or c in " -_" else "_" for c in nome_musica)
    seguro = seguro.strip().replace(" ", "_")
    return f"{seguro or 'sessao'}.wav"


def publicar_musica(
    caminho_wav: str | Path,
    nome: str,
    autor: str,
    url_base: str | None = None,
    camadas: list | None = None,
) -> bool:
    """
    Envia o WAV direto pro backend da playlist (/musicas/upload),
    exatamente como o publicar.html faz. Retorna True se publicou com sucesso.
    """
    if url_base is None:
        url_base = resolver_url_backend()
    url = f"{url_base}/musicas/upload"
    print(f"\n[PUBLICAÇÃO] Publicando '{nome}' em {url} ...")

    try:
        caminho = Path(caminho_wav)
        if not caminho.exists():
            print(f"[ERRO] Arquivo não encontrado: {caminho}")
            return False

        with open(caminho, "rb") as f:
            files = {"arquivo": (caminho.name, f, "audio/wav")}
            data = {"nome": nome, "autor": autor}
            if camadas is not None:
                data["camadas"] = json.dumps(camadas, ensure_ascii=False) if not isinstance(camadas, str) else camadas
            resposta = requests.post(url, data=data, files=files, timeout=120)
    except requests.exceptions.RequestException as e:
        print(f"[ERRO] Não consegui conectar no backend: {e}")
        print(f"O arquivo continua salvo localmente em: {caminho_wav}")
        return False

    if resposta.ok:
        try:
            payload = resposta.json()
        except Exception:
            payload = {}
        print(f"[PUBLICADO] '{payload.get('nome', nome)}' já está na playlist pública!")
        return True

    try:
        detalhe = resposta.json().get("detail", resposta.text)
    except Exception:
        detalhe = resposta.text
    print(f"[ERRO] O backend recusou a publicação: {detalhe}")
    print(f"O arquivo continua salvo localmente em: {caminho_wav}")
    return False


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="Rock Machine - Aplicação Física",
    description="Servidor local da máquina física Rock Machine com RFID e suporte manual.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# ESTADO DA SESSÃO E MOTOR DE ÁUDIO
# =========================================================

estado_sessao = {
    "gravando": False,
    "inicio": None,
    "tempo": 0.0,
    "camadas": [],
    "arquivo_wav": None,
    "sessao_id": None,
    "publicada": False,
    "arduino_conectado": False,
}

# Tipos ativos por posição: "normal" ou "efeito"
_tipo_ativo_por_posicao: dict[int, str] = {}

lock = threading.Lock()

player = RockMachinePlayer(
    PASTA_STEMS,
    BPM_ALVO,
    COMPASSOS_POR_LOOP,
    TEMPOS_POR_COMPASSO,
)


# =========================================================
# MODELOS DE REQUISIÇÃO
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
# GERENCIAMENTO DE CAMADAS E ESTADO
# =========================================================

def atualizar_tempo():
    """Calcula o tempo atual da gravação."""
    if estado_sessao["gravando"] and estado_sessao["inicio"]:
        estado_sessao["tempo"] = time.time() - estado_sessao["inicio"]


def registrar_camada(uid: str, posicao: int) -> dict | None:
    """Registra uma camada musical associada a uma posição na sessão."""
    dados = UID_PARA_MUSICA.get(uid)
    if not dados:
        return None

    if isinstance(dados, dict):
        musica_nome = dados.get("musica", "")
        artista_nome = dados.get("artista", "")
        tipo_inst = dados.get("tipo", INSTRUMENTO_POR_POSICAO.get(posicao, "other"))
    else:
        musica_nome = str(dados)
        artista_nome = ARTISTA_POR_MUSICA.get(musica_nome, "")
        tipo_inst = INSTRUMENTO_POR_POSICAO.get(posicao, "other")

    camada = {
        "uid": uid,
        "posicao": posicao,
        "musica": musica_nome,
        "artista": artista_nome,
        "tipo": tipo_inst,
        "ativa": True,
    }

    # Substitui qualquer camada prévia na mesma posição
    estado_sessao["camadas"] = [
        c for c in estado_sessao["camadas"] if c["posicao"] != posicao
    ]
    estado_sessao["camadas"].append(camada)
    return camada


def remover_camada(posicao: int) -> None:
    """Remove a camada associada à posição."""
    estado_sessao["camadas"] = [
        c for c in estado_sessao["camadas"] if c["posicao"] != posicao
    ]


# =========================================================
# PROCESSAMENTO DE EVENTOS DO HARDWARE (ARDUINO / RFID)
# =========================================================

def processar_evento_hardware(linha: str) -> None:
    """Processa eventos seriais dos leitores RFID (ON:pos:uid ou OFF:pos)."""
    linha = linha.strip()
    if not linha:
        return

    if linha == "READY":
        print("[HARDWARE] Arduino conectado e pronto para leitura.")
        return

    # -----------------------------------------------------
    # EVENTO ON: Tag colocada no leitor RFID
    # -----------------------------------------------------
    if linha.startswith("ON:"):
        partes = linha.split(":")
        if len(partes) != 3:
            print(f"[HARDWARE - AVISO] Linha ON inválida: {linha}")
            return

        _, pos_str, uid = partes
        try:
            posicao = int(pos_str)
        except ValueError:
            return

        uid = uid.strip()

        # Ficha de efeito sonoro imediato
        if uid in UID_PARA_EFEITO:
            musica, arquivo = UID_PARA_EFEITO[uid]
            player.iniciar_efeito(posicao, musica, arquivo)
            _tipo_ativo_por_posicao[posicao] = "efeito"
            print(f"[HARDWARE] ⚡ Efeito ativado na posição {posicao}: {musica} / {arquivo}")
            return

        # Ficha de música/stem sincronizado
        dados_musica = UID_PARA_MUSICA.get(uid)
        if dados_musica:
            instrumento = INSTRUMENTO_POR_POSICAO.get(posicao)
            if not instrumento:
                print(f"[HARDWARE - AVISO] Posição {posicao} sem instrumento mapeado.")
                return

            nome_musica = dados_musica.get("musica") if isinstance(dados_musica, dict) else str(dados_musica)
            player.colocar_ficha(posicao, nome_musica, instrumento)
            _tipo_ativo_por_posicao[posicao] = "normal"

            with lock:
                registrar_camada(uid, posicao)

            print(
                f"[HARDWARE] 🎵 RFID detectado na posição {posicao} ({instrumento}): "
                f"{nome_musica} [UID: {uid}]"
            )
            return

        print(f"[HARDWARE - AVISO] Tag UID {uid} não cadastrada em UID_PARA_MUSICA ou UID_PARA_EFEITO.")

    # -----------------------------------------------------
    # EVENTO OFF: Tag retirada do leitor RFID
    # -----------------------------------------------------
    elif linha.startswith("OFF:"):
        partes = linha.split(":")
        if len(partes) != 2:
            print(f"[HARDWARE - AVISO] Linha OFF inválida: {linha}")
            return

        try:
            posicao = int(partes[1])
        except ValueError:
            return

        tipo = _tipo_ativo_por_posicao.pop(posicao, None)
        if tipo == "efeito":
            player.parar_efeito(posicao)
            print(f"[HARDWARE] ⏹ Efeito parado na posição {posicao}.")
        else:
            player.retirar_ficha(posicao)
            print(f"[HARDWARE] ⏹ Ficha retirada da posição {posicao}.")

        with lock:
            remover_camada(posicao)

    else:
        print(f"[HARDWARE - SERIAL] {linha}")


# =========================================================
# THREAD DE LEITURA SERIAL EM BACKGROUND
# =========================================================

stop_hardware_event = threading.Event()
leitores_hardware: LeitoresSerial | None = None


def thread_escuta_serial(stop_event: threading.Event) -> None:
    """Escuta continuamente as portas seriais dos Arduinos em background com auto-reconexão."""
    global leitores_hardware
    leitores = LeitoresSerial(PORTAS_SERIAIS, BAUD_RATE)
    leitores_hardware = leitores
    leitores.iniciar()
    print(f"[SERIAL] Gerenciador serial ativo em {len(PORTAS_SERIAIS)} porta(s): {', '.join(PORTAS_SERIAIS)} ({BAUD_RATE} baud)")

    while not stop_event.is_set():
        with lock:
            estado_sessao["arduino_conectado"] = len(leitores.conectadas) > 0

        try:
            linha = leitores.fila.get(timeout=0.2)
            processar_evento_hardware(linha)
        except queue.Empty:
            continue
        except Exception as e:
            print(f"[SERIAL - ERRO] Falha ao processar evento serial: {e}")

    leitores.fechar()


# =========================================================
# THREAD DE MONITORAMENTO DE TEMPO LIMITE DE GRAVAÇÃO
# =========================================================

def monitor_tempo_gravacao(stop_event: threading.Event) -> None:
    """Finaliza a gravação automaticamente caso ultrapasse a duração máxima."""
    while not stop_event.is_set():
        with lock:
            if (
                estado_sessao["gravando"]
                and estado_sessao["inicio"] is not None
                and (time.time() - estado_sessao["inicio"]) >= DURACAO_MAXIMA_GRAVACAO
            ):
                print(f"[GRAVAÇÃO] Limite máximo ({DURACAO_MAXIMA_GRAVACAO:.1f}s) atingido. Finalizando...")
                duracao = DURACAO_MAXIMA_GRAVACAO
                sessao_id = estado_sessao["sessao_id"]
                caminho_wav = PASTA_GRAVACOES / f"rockmachine_{sessao_id}.wav"

                player.parar_gravacao_e_salvar(str(caminho_wav), duracao)
                player.parar_todos_os_sons()

                estado_sessao["gravando"] = False
                estado_sessao["inicio"] = None
                estado_sessao["tempo"] = duracao
                estado_sessao["arquivo_wav"] = str(caminho_wav)

        time.sleep(0.5)


# =========================================================
# ROTAS DA INTERFACE WEB
# =========================================================

@app.get("/maquina")
def pagina_maquina():
    """Interface principal da Rock Machine."""
    caminho = FRONTEND_DIR / "maquina.html"
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="maquina.html não encontrado.")
    return FileResponse(caminho)


@app.get("/publicar")
def pagina_publicar():
    """Interface de finalização e créditos para publicação."""
    caminho = FRONTEND_DIR / "publicar.html"
    if not caminho.exists():
        raise HTTPException(status_code=404, detail="publicar.html não encontrado.")
    return FileResponse(caminho)


# =========================================================
# ROTAS DE SESSÃO & CONTROLE
# =========================================================

@app.get("/sessao/status")
def obter_status():
    """Retorna o estado atual da gravação, tempo e camadas ativas."""
    with lock:
        atualizar_tempo()
        return {
            "gravando": estado_sessao["gravando"],
            "tempo": round(estado_sessao["tempo"], 1),
            "camadas": estado_sessao["camadas"],
            "arquivo_wav": estado_sessao["arquivo_wav"],
            "sessao_id": estado_sessao["sessao_id"],
            "publicada": estado_sessao["publicada"],
            "arduino_conectado": estado_sessao["arduino_conectado"],
        }


@app.post("/sessao/iniciar")
def iniciar_sessao():
    """Inicia uma nova sessão de gravação musical."""
    with lock:
        if estado_sessao["gravando"]:
            raise HTTPException(status_code=400, detail="Já existe uma sessão em andamento.")

        estado_sessao["gravando"] = True
        estado_sessao["inicio"] = time.time()
        estado_sessao["tempo"] = 0.0
        estado_sessao["arquivo_wav"] = None
        estado_sessao["sessao_id"] = str(uuid.uuid4())
        estado_sessao["publicada"] = False

        player.iniciar_gravacao()
        print(f"[SESSÃO] Nova gravação iniciada: ID {estado_sessao['sessao_id']}")

        return {
            "sucesso": True,
            "sessao_id": estado_sessao["sessao_id"],
        }


@app.post("/sessao/finalizar")
def finalizar_sessao():
    """Finaliza a sessão atual, renderiza o áudio final em WAV e para os sons ao vivo."""
    with lock:
        if not estado_sessao["gravando"]:
            raise HTTPException(status_code=400, detail="Não existe uma sessão sendo gravada.")

        atualizar_tempo()
        duracao = estado_sessao["tempo"]
        if duracao <= 0:
            duracao = 1.0

        sessao_id = estado_sessao["sessao_id"]
        caminho_wav = PASTA_GRAVACOES / f"rockmachine_{sessao_id}.wav"

        # 1. Renderiza matematicamente o WAV final com todos os eventos
        player.parar_gravacao_e_salvar(str(caminho_wav), duracao)

        # 2. Para a reprodução ao vivo
        player.parar_todos_os_sons()

        # 3. Atualiza estado
        estado_sessao["gravando"] = False
        estado_sessao["inicio"] = None
        estado_sessao["tempo"] = duracao
        estado_sessao["arquivo_wav"] = str(caminho_wav)

        print(f"[SESSÃO] Sessão finalizada. WAV salvo em: {caminho_wav.resolve()}")

        return {
            "sucesso": True,
            "arquivo_wav": str(caminho_wav),
            "tempo": round(duracao, 1),
            "camadas": estado_sessao["camadas"],
        }


@app.get("/sessao/finalizada")
def obter_sessao_finalizada():
    """Retorna os dados da última sessão finalizada para a tela de publicação."""
    with lock:
        caminho_wav = estado_sessao["arquivo_wav"]
        if not caminho_wav:
            raise HTTPException(status_code=404, detail="Nenhuma sessão finalizada encontrada.")

        caminho = Path(caminho_wav)
        if not caminho.exists():
            raise HTTPException(status_code=404, detail="O arquivo WAV da sessão não foi encontrado.")

        return {
            "arquivo_wav": str(caminho),
            "tempo": round(estado_sessao["tempo"], 1),
            "camadas": estado_sessao["camadas"],
            "sessao_id": estado_sessao["sessao_id"],
            "publicada": estado_sessao["publicada"],
        }


@app.post("/sessao/publicar")
def publicar_sessao(dados: DadosPublicacao):
    """Envia a gravação e metadados ao backend para entrar na playlist pública."""
    nome = dados.nome.strip()
    autor = dados.autor.strip()

    if not nome:
        raise HTTPException(status_code=400, detail="Informe o nome da música.")
    if not autor:
        raise HTTPException(status_code=400, detail="Informe o nome do autor.")

    with lock:
        caminho_wav = estado_sessao["arquivo_wav"]
        camadas = list(estado_sessao["camadas"])
        publicada = estado_sessao["publicada"]

    if not caminho_wav:
        raise HTTPException(status_code=400, detail="Nenhuma música foi finalizada.")
    if publicada:
        raise HTTPException(status_code=400, detail="Esta sessão já foi publicada.")

    caminho = Path(caminho_wav)
    if not caminho.exists():
        raise HTTPException(status_code=404, detail=f"Arquivo WAV não encontrado: {caminho}")

    backend_base = resolver_url_backend()
    url = f"{backend_base}/musicas/upload"
    print(f"[PUBLICAÇÃO] Enviando música '{nome}' ({autor}) para: {url}")

    try:
        with open(caminho, "rb") as arquivo:
            files = {
                "arquivo": (caminho.name, arquivo, "audio/wav")
            }
            data = {
                "nome": nome,
                "autor": autor,
                "camadas": json.dumps(camadas, ensure_ascii=False),
            }
            resposta = requests.post(url, data=data, files=files, timeout=120)

    except requests.exceptions.RequestException as erro:
        print(f"[ERRO DE CONEXÃO] Falha ao enviar para {url}: {erro}")
        raise HTTPException(
            status_code=502,
            detail=f"Não foi possível conectar ao backend ({backend_base}): {erro}",
        )

    if not resposta.ok:
        try:
            detalhe = resposta.json().get("detail", "Erro desconhecido no backend.")
        except Exception:
            detalhe = resposta.text
        raise HTTPException(status_code=502, detail=f"O backend recusou a publicação: {detalhe}")

    try:
        resultado = resposta.json()
    except Exception:
        resultado = {"mensagem": resposta.text}

    with lock:
        estado_sessao["publicada"] = True

    print(f"[PUBLICAÇÃO] Sucesso! Música registrada na playlist pública.")
    return {
        "sucesso": True,
        "mensagem": "Música publicada com sucesso!",
        "musica": resultado,
    }


@app.post("/sessao/cancelar")
def cancelar_sessao():
    """Cancela a gravação em andamento e descarta os dados."""
    with lock:
        if not estado_sessao["gravando"]:
            raise HTTPException(status_code=400, detail="Não existe uma sessão em andamento.")

        player._gravando = False
        player._eventos_gravacao = []
        player.parar_todos_os_sons()

        estado_sessao["gravando"] = False
        estado_sessao["inicio"] = None
        estado_sessao["tempo"] = 0.0
        estado_sessao["camadas"] = []
        estado_sessao["arquivo_wav"] = None
        estado_sessao["sessao_id"] = None
        estado_sessao["publicada"] = False

        print("[SESSÃO] Gravação cancelada.")
        return {"sucesso": True, "mensagem": "Sessão cancelada."}


# =========================================================
# CONTROLES MANUAIS (FALLBACK E DEMONSTRAÇÃO WEB)
# =========================================================

@app.post("/simulacao/adicionar")
def simulacao_adicionar(dados: SimulacaoAdicionar):
    """Permite selecionar uma faixa manualmente pela interface web."""
    uid = dados.uid.strip()
    musica_dado = UID_PARA_MUSICA.get(uid)
    if not musica_dado:
        raise HTTPException(status_code=404, detail=f"UID não encontrado: {uid}")

    posicao = dados.posicao
    instrumento = INSTRUMENTO_POR_POSICAO.get(posicao)
    if not instrumento:
        raise HTTPException(status_code=400, detail="Posição inválida. Use 0 (bateria), 1 (outros) ou 2 (vocal).")

    nome_musica = musica_dado.get("musica") if isinstance(musica_dado, dict) else str(musica_dado)
    player.colocar_ficha(posicao, nome_musica, instrumento)
    _tipo_ativo_por_posicao[posicao] = "normal"

    with lock:
        camada = registrar_camada(uid, posicao)

    print(f"[MANUAL] Camada adicionada na posição {posicao} ({instrumento}): {nome_musica}")
    return {"sucesso": True, "camada": camada}


@app.post("/simulacao/remover")
def simulacao_remover(dados: SimulacaoRemover):
    """Permite remover uma faixa manualmente pela interface web."""
    posicao = dados.posicao
    tipo = _tipo_ativo_por_posicao.pop(posicao, None)
    if tipo == "efeito":
        player.parar_efeito(posicao)
    else:
        player.retirar_ficha(posicao)

    with lock:
        remover_camada(posicao)

    print(f"[MANUAL] Camada removida da posição {posicao}.")
    return {"sucesso": True, "posicao": posicao}


@app.get("/fichas/disponiveis")
def listar_fichas_disponiveis():
    """Retorna todas as fichas e stems cadastrados para a seleção manual."""
    fichas = []
    for uid, info in UID_PARA_MUSICA.items():
        if isinstance(info, dict):
            musica = info.get("musica", "")
            artista = info.get("artista", "")
            tipo = info.get("tipo", "other")
        else:
            musica = str(info)
            artista = ARTISTA_POR_MUSICA.get(musica, "")
            tipo = "drums"
        fichas.append({
            "uid": uid,
            "musica": musica,
            "artista": artista,
            "tipo": tipo,
            "posicao": 0 if tipo == "drums" else (1 if tipo == "other" else 2),
        })
    return fichas


# =========================================================
# EXECUÇÃO PRINCIPAL
# =========================================================

def main():
    print()
    print("=========================================================")
    print("         ROCK MACHINE - APLICAÇÃO FÍSICA & SESSÃO        ")
    print("=========================================================")
    print(f" Servidor Web:      http://{HOST_LOCAL}:{PORTA_LOCAL}")
    print(f" Interface Máquina: http://{HOST_LOCAL}:{PORTA_LOCAL}/maquina")
    print(f" Tela Publicação:   http://{HOST_LOCAL}:{PORTA_LOCAL}/publicar")
    print(f" Portas Seriais:    {', '.join(PORTAS_SERIAIS)} ({BAUD_RATE} baud)")
    print(f" Backend Destino:   {resolver_url_backend()}")
    print("=========================================================")
    print()

    # Inicia a thread de escuta serial dos Arduinos
    serial_thread = threading.Thread(
        target=thread_escuta_serial,
        args=(stop_hardware_event,),
        daemon=True,
    )
    serial_thread.start()

    # Inicia o monitor de tempo de gravação
    monitor_thread = threading.Thread(
        target=monitor_tempo_gravacao,
        args=(stop_hardware_event,),
        daemon=True,
    )
    monitor_thread.start()

    # Inicia o stream de áudio contínuo e roda o servidor FastAPI
    try:
        with player.iniciar_stream():
            uvicorn.run(
                app,
                host=HOST_LOCAL,
                port=PORTA_LOCAL,
                log_level="info",
            )
    except KeyboardInterrupt:
        print("\n[ENCERRANDO] Fechando aplicação...")
    finally:
        stop_hardware_event.set()
        player.parar_todos_os_sons()
        print("[ENCERRADO] Rock Machine finalizada com sucesso.")


if __name__ == "__main__":
    main()