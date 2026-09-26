"""
RockMachine - Gravar uma sessão e publicar direto na playlist
====================================================================
Roda igual o ponte_serial.py (escuta o Arduino, toca o que as fichas
determinam), grava por uma DURAÇÃO FIXA (em compassos) e, quando
terminar, já ENVIA automaticamente pro backend da playlist — sem
precisar abrir o publicar.html manualmente.

Teclas:
    r   -> pede nome da música e do autor, começa a gravar. Para e
           publica sozinho quando o tempo definido em COMPASSOS_GRAVACAO
           acabar.
    s   -> cancela a gravação atual antes da hora (não salva, não publica)
    ESC -> sai do programa

Requisitos: pip install pyserial sounddevice soundfile numpy requests
IMPORTANTE: usa 'msvcrt' (exclusivo do Windows).
"""

import sys
import time
from pathlib import Path

import requests
import serial

from rockmachine_motor import (
    RockMachinePlayer,
    BPM_ALVO,
    COMPASSOS_POR_LOOP,
    TEMPOS_POR_COMPASSO,
    PASTA_STEMS,
    calcular_duracao_loop_segundos,
)
from ponte_serial import processar_linha, PORTA_SERIAL, BAUD_RATE

try:
    import msvcrt
    WINDOWS = True
except ImportError:
    WINDOWS = False

# ============ CONFIGURAÇÕES ============

# 64 compassos = 4 repetições do loop de 16 compassos (~2min a 117 BPM)
COMPASSOS_GRAVACAO = 64

PASTA_GRAVACOES = "gravacoes"

# URL base do backend da playlist. Trocar para "http://127.0.0.1:8000"
# (ou a porta que o server.py do Leonardo usar) se quiserem testar local
# em vez de publicar direto no ambiente hospedado no Render.
URL_BASE_PLAYLIST = "https://rock-machine.onrender.com"

# ======================================================


def pedir_dados_publicacao():
    print()
    nome = input("Nome da música: ").strip() or time.strftime("Sessao %Y-%m-%d %H:%M")
    autor = input("Nome do autor: ").strip() or "RockMachine"
    return nome, autor


def nome_para_arquivo(nome_musica):
    """Transforma o nome da música num nome de arquivo seguro."""
    seguro = "".join(c if c.isalnum() or c in " -_" else "_" for c in nome_musica)
    seguro = seguro.strip().replace(" ", "_")
    return f"{seguro or 'sessao'}.wav"


def publicar_musica(caminho_wav, nome, autor, url_base=URL_BASE_PLAYLIST):
    """Envia o WAV direto pro backend da playlist (rota /musicas/upload),
    exatamente como o publicar.html faz — só que sem precisar abrir o
    navegador. Retorna True se publicou com sucesso."""
    url = f"{url_base}/musicas/upload"
    print(f"\nPublicando '{nome}' em {url} ...")

    try:
        with open(caminho_wav, "rb") as f:
            files = {"arquivo": (Path(caminho_wav).name, f, "audio/wav")}
            data = {"nome": nome, "autor": autor}
            # timeout maior: serviços gratuitos tipo Render "dormem" e podem
            # demorar uns 30-50s pra responder na primeira requisição
            resposta = requests.post(url, data=data, files=files, timeout=60)
    except requests.exceptions.RequestException as e:
        print(f"[ERRO] Não consegui conectar no backend: {e}")
        print(f"O arquivo continua salvo localmente em: {caminho_wav}")
        return False

    if resposta.ok:
        payload = resposta.json()
        print(f"[PUBLICADO] '{payload.get('nome', nome)}' já está na playlist!")
        return True

    try:
        detalhe = resposta.json().get("detail", "Erro desconhecido")
    except Exception:
        detalhe = resposta.text
    print(f"[ERRO] O backend recusou a publicação: {detalhe}")
    print(f"O arquivo continua salvo localmente em: {caminho_wav}")
    return False


def main():
    if not WINDOWS:
        print("AVISO: 'msvcrt' não disponível (isso não é Windows). Rode no Windows.")
        return

    Path(PASTA_GRAVACOES).mkdir(exist_ok=True)

    duracao_gravacao_seg = calcular_duracao_loop_segundos(
        BPM_ALVO, COMPASSOS_GRAVACAO, TEMPOS_POR_COMPASSO
    )
    print(f"Duração fixa de cada gravação: {duracao_gravacao_seg:.1f}s "
          f"({COMPASSOS_GRAVACAO} compassos a {BPM_ALVO} BPM)")
    print(f"Publicando em: {URL_BASE_PLAYLIST}/musicas/upload")

    print(f"Conectando na porta {PORTA_SERIAL} ({BAUD_RATE} baud)...")
    try:
        arduino = serial.Serial(PORTA_SERIAL, BAUD_RATE, timeout=0.1)
    except serial.SerialException as e:
        print(f"[ERRO] Não consegui abrir a porta serial: {e}")
        sys.exit(1)

    time.sleep(2)

    print("Carregando motor de áudio...")
    player = RockMachinePlayer(PASTA_STEMS, BPM_ALVO, COMPASSOS_POR_LOOP, TEMPOS_POR_COMPASSO)

    gravando = False
    caminho_saida = None
    nome_musica = None
    nome_autor = None
    tempo_inicio = None

    with player.iniciar_stream():
        print("\n=== RockMachine — Gravação com publicação automática ===")
        print("Interaja com as fichas normalmente.")
        print("  'r' -> começar a gravar (publica sozinho quando o tempo acabar)")
        print("  's' -> cancelar a gravação atual (não salva, não publica)")
        print("  ESC -> sair")
        print()

        try:
            while True:
                linha = arduino.readline().decode(errors="ignore")
                if linha:
                    processar_linha(linha, player)

                if gravando and (time.time() - tempo_inicio) >= duracao_gravacao_seg:
                    player.parar_gravacao_e_salvar(caminho_saida, duracao_gravacao_seg)
                    gravando = False
                    print(f"Gravação salva localmente: {caminho_saida.resolve()}")
                    publicar_musica(caminho_saida, nome_musica, nome_autor)

                if msvcrt.kbhit():
                    tecla = msvcrt.getch().decode(errors="ignore").lower()

                    if tecla == "\x1b":
                        print("\nEncerrando...")
                        break

                    if tecla == "r":
                        if gravando:
                            print("[AVISO] Já está gravando.")
                        else:
                            nome_musica, nome_autor = pedir_dados_publicacao()
                            caminho_saida = Path(PASTA_GRAVACOES) / nome_para_arquivo(nome_musica)
                            player.iniciar_gravacao()
                            tempo_inicio = time.time()
                            gravando = True
                            print(f"Gravando por {duracao_gravacao_seg:.1f}s — pode interagir com as fichas agora.")

                    elif tecla == "s":
                        if not gravando:
                            print("[AVISO] Não está gravando.")
                        else:
                            player.parar_gravacao_e_salvar(None, duracao_gravacao_seg)
                            gravando = False
                            print("[GRAVAÇÃO] Cancelada, nada foi salvo nem publicado.")

        except KeyboardInterrupt:
            print("\nEncerrando...")
        finally:
            arduino.close()


if __name__ == "__main__":
    main()