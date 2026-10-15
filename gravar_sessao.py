"""
RockMachine - Gravar uma sessão
====================================================================
Roda igual o ponte_serial.py (escuta o Arduino e toca o que as fichas
determinam), grava por uma DURAÇÃO FIXA (em compassos) e disponibiliza
uma interface web local para controlar a máquina.

Nesta etapa:
    - FastAPI roda dentro deste mesmo arquivo
    - /maquina abre o maquina.html
    - A gravação continua sendo controlada pelo programa
    - A publicação pelo terminal foi removida

Teclas:
    r   -> começa a gravar
    s   -> cancela a gravação atual
    ESC -> sai do programa

Interface web:
    http://127.0.0.1:8001/maquina

Requisitos:
    pip install pyserial sounddevice soundfile numpy requests fastapi uvicorn

IMPORTANTE:
    usa 'msvcrt' (exclusivo do Windows).
"""

import sys
import time
import threading
from pathlib import Path

import serial
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from rockmachine_motor import (
    RockMachinePlayer,
    BPM_ALVO,
    COMPASSOS_POR_LOOP,
    TEMPOS_POR_COMPASSO,
    PASTA_STEMS,
    calcular_duracao_loop_segundos,
)

from ponte_serial import (
    processar_linha,
    PORTA_SERIAL,
    BAUD_RATE,
)


try:
    import msvcrt
    WINDOWS = True
except ImportError:
    WINDOWS = False


# ======================================================
# CONFIGURAÇÕES
# ======================================================

# 64 compassos = 4 repetições do loop de 16 compassos
# aproximadamente 2 minutos a 117 BPM
COMPASSOS_GRAVACAO = 64

PASTA_GRAVACOES = "gravacoes"

# Servidor local da Rock Machine
HOST_LOCAL = "127.0.0.1"
PORTA_LOCAL = 8001


# ======================================================
# FASTAPI
# ======================================================

app = FastAPI(
    title="RockMachine Local",
    description="Servidor local da máquina RockMachine",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"


@app.get("/maquina")
def pagina_maquina():
    """
    Abre a interface principal da Rock Machine.
    """
    caminho = FRONTEND_DIR / "maquina.html"

    if not caminho.exists():
        return {
            "erro": "maquina.html não encontrado",
            "caminho_esperado": str(caminho),
        }

    return FileResponse(caminho)


# ======================================================
# SERVIDOR FASTAPI
# ======================================================

def iniciar_servidor():
    """
    Inicia o FastAPI em uma thread separada para que o programa
    continue escutando o Arduino normalmente.
    """

    print()
    print("==============================================")
    print(" ROCK MACHINE - SERVIDOR LOCAL")
    print("==============================================")
    print(f" Interface: http://{HOST_LOCAL}:{PORTA_LOCAL}/maquina")
    print("==============================================")
    print()

    uvicorn.run(
        app,
        host=HOST_LOCAL,
        port=PORTA_LOCAL,
        log_level="warning",
    )


# ======================================================
# ARQUIVO DE GRAVAÇÃO
# ======================================================

def nome_para_arquivo(nome_musica):
    """
    Transforma o nome da música em um nome de arquivo seguro.
    """

    seguro = "".join(
        c if c.isalnum() or c in " -_" else "_"
        for c in nome_musica
    )

    seguro = seguro.strip().replace(" ", "_")

    return f"{seguro or 'sessao'}.wav"


# ======================================================
# MAIN
# ======================================================

def main():

    if not WINDOWS:
        print(
            "AVISO: 'msvcrt' não disponível "
            "(isso não é Windows). Rode no Windows."
        )
        return

    # --------------------------------------------------
    # Pasta das gravações
    # --------------------------------------------------

    Path(PASTA_GRAVACOES).mkdir(exist_ok=True)


    # --------------------------------------------------
    # Duração da gravação
    # --------------------------------------------------

    duracao_gravacao_seg = calcular_duracao_loop_segundos(
        BPM_ALVO,
        COMPASSOS_GRAVACAO,
        TEMPOS_POR_COMPASSO,
    )

    print(
        f"Duração fixa de cada gravação: "
        f"{duracao_gravacao_seg:.1f}s "
        f"({COMPASSOS_GRAVACAO} compassos a {BPM_ALVO} BPM)"
    )


    # --------------------------------------------------
    # Inicia servidor FastAPI
    # --------------------------------------------------

    servidor = threading.Thread(
        target=iniciar_servidor,
        daemon=True,
    )

    servidor.start()


    # --------------------------------------------------
    # Conecta ao Arduino
    # --------------------------------------------------

    print(
        f"Conectando na porta "
        f"{PORTA_SERIAL} ({BAUD_RATE} baud)..."
    )

    try:
        arduino = serial.Serial(
            PORTA_SERIAL,
            BAUD_RATE,
            timeout=0.1,
        )

    except serial.SerialException as e:
        print(
            f"[ERRO] Não consegui abrir "
            f"a porta serial: {e}"
        )

        sys.exit(1)


    # --------------------------------------------------
    # Aguarda Arduino inicializar
    # --------------------------------------------------

    time.sleep(2)


    # --------------------------------------------------
    # Carrega motor de áudio
    # --------------------------------------------------

    print("Carregando motor de áudio...")

    player = RockMachinePlayer(
        PASTA_STEMS,
        BPM_ALVO,
        COMPASSOS_POR_LOOP,
        TEMPOS_POR_COMPASSO,
    )


    # --------------------------------------------------
    # Estado da gravação
    # --------------------------------------------------

    gravando = False

    caminho_saida = None

    tempo_inicio = None


    # --------------------------------------------------
    # Inicia stream de áudio
    # --------------------------------------------------

    with player.iniciar_stream():

        print()
        print("==============================================")
        print("       ROCK MACHINE - PRONTO")
        print("==============================================")
        print()
        print(
            f"Interface web:"
            f" http://{HOST_LOCAL}:{PORTA_LOCAL}/maquina"
        )
        print()
        print("Controles temporários pelo teclado:")
        print("  r   -> começar a gravar")
        print("  s   -> cancelar a gravação")
        print("  ESC -> sair")
        print()
        print("Interaja com as fichas normalmente.")
        print()


        try:

            while True:

                # ==================================================
                # LÊ O ARDUINO
                # ==================================================

                linha = arduino.readline().decode(
                    errors="ignore"
                )

                if linha:
                    processar_linha(
                        linha,
                        player,
                    )


                # ==================================================
                # FINALIZAÇÃO AUTOMÁTICA POR TEMPO
                # ==================================================

                if (
                    gravando
                    and tempo_inicio is not None
                    and (
                        time.time() - tempo_inicio
                    ) >= duracao_gravacao_seg
                ):

                    player.parar_gravacao_e_salvar(
                        caminho_saida,
                        duracao_gravacao_seg,
                    )

                    gravando = False

                    print()
                    print(
                        "[GRAVAÇÃO] Finalizada automaticamente."
                    )

                    print(
                        f"Arquivo salvo em:"
                        f" {caminho_saida.resolve()}"
                    )

                    print()
                    print(
                        "A próxima etapa será disponibilizar "
                        "essa gravação para publicação pela interface web."
                    )

                    print()


                # ==================================================
                # TECLADO
                # ==================================================

                if msvcrt.kbhit():

                    tecla = (
                        msvcrt.getch()
                        .decode(errors="ignore")
                        .lower()
                    )


                    # ----------------------------------------------
                    # ESC
                    # ----------------------------------------------

                    if tecla == "\x1b":

                        print()
                        print("Encerrando...")
                        break


                    # ----------------------------------------------
                    # R - COMEÇAR GRAVAÇÃO
                    # ----------------------------------------------

                    if tecla == "r":

                        if gravando:

                            print(
                                "[AVISO] Já está gravando."
                            )

                        else:

                            # Por enquanto usamos um nome automático.
                            # Depois o publicar.html vai receber o
                            # nome escolhido pelo usuário.

                            nome_arquivo = (
                                f"sessao_"
                                f"{time.strftime('%Y%m%d_%H%M%S')}"
                            )

                            caminho_saida = (
                                Path(PASTA_GRAVACOES)
                                / nome_para_arquivo(
                                    nome_arquivo
                                )
                            )

                            player.iniciar_gravacao()

                            tempo_inicio = time.time()

                            gravando = True

                            print()
                            print(
                                "=============================================="
                            )
                            print("       GRAVAÇÃO INICIADA")
                            print(
                                "=============================================="
                            )
                            print(
                                f"Duração: "
                                f"{duracao_gravacao_seg:.1f}s"
                            )
                            print(
                                "Interaja com as fichas agora."
                            )
                            print()


                    # ----------------------------------------------
                    # S - CANCELAR
                    # ----------------------------------------------

                    elif tecla == "s":

                        if not gravando:

                            print(
                                "[AVISO] "
                                "Não está gravando."
                            )

                        else:

                            player.parar_gravacao_e_salvar(
                                None,
                                duracao_gravacao_seg,
                            )

                            gravando = False

                            caminho_saida = None

                            tempo_inicio = None

                            print()
                            print(
                                "[GRAVAÇÃO] "
                                "Cancelada."
                            )
                            print(
                                "Nada foi salvo ou publicado."
                            )
                            print()


        except KeyboardInterrupt:

            print()
            print("Encerrando...")


        finally:

            arduino.close()

            print()
            print("Porta serial fechada.")
            print("Rock Machine encerrada.")


# ======================================================
# EXECUÇÃO
# ======================================================

if __name__ == "__main__":
    main()