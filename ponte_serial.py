"""
RockMachine - Ponte serial (Arduino -> motor de áudio)
=========================================================
Lê os eventos ON:/OFF: dos 3 leitores fixos e aciona o motor de áudio.

Cada POSIÇÃO (leitor) tem um instrumento fixo — baixo é ignorado:
    0 = bateria (drums)
    1 = vocal   (vocals)
    2 = outros  (other)

Cada FICHA (UID) é ou:
    - uma MÚSICA normal (toca o stem do instrumento daquela posição,
      sincronizado em loop) -> cadastre em UID_PARA_MUSICA
    - um EFEITO imediato (toca uma vez, sem sincronizar, começa ao
      colocar e para ao retirar) -> cadastre em UID_PARA_EFEITO

Requisitos: pip install pyserial sounddevice soundfile numpy
"""

import sys
import time

import serial

from rockmachine_motor import RockMachinePlayer, BPM_ALVO, COMPASSOS_POR_LOOP, TEMPOS_POR_COMPASSO, PASTA_STEMS

# ============ CONFIGURAÇÕES ============

PORTA_SERIAL = "COM5"
BAUD_RATE = 9600

# Instrumento fixo de cada posição (índice do leitor no Arduino).
# Baixo não entra — só bateria, vocal e outros.
INSTRUMENTO_POR_POSICAO = {
    0: "drums",
    1: "vocals",
    2: "other",
}

# UID de ficha "normal" -> nome da música (a pasta dentro de stems_prontos/)
UID_PARA_MUSICA = {
    "73660EF7": "Smells Like Teen Spirit",
    "41CA4B73": "Tempos Modernos",
    "F9376A48": "Billie Jean",
    "1AD3781A": "Smells Like Teen Spirit",
    "2A634C73": "Tempos Modernos",
    "21615573": "Payphone",
    "86AF4183": "In the End",
    # adicione mais linhas aqui conforme cadastrarem novas fichas
}

# UID de ficha "efeito" -> (pasta da música, nome do arquivo de efeito)
# O arquivo precisa existir em stems_prontos/<pasta>/<arquivo>.wav
UID_PARA_EFEITO = {
    "4BA49079": ("Another One Bites the Dust", "gemido-whatsapp"),
}

# ======================================================


# posicao_id -> "normal" ou "efeito", pra saber qual método chamar no OFF
_tipo_ativo_por_posicao = {}


def processar_linha(linha, player):
    linha = linha.strip()
    if not linha:
        return

    if linha == "READY":
        print("[Arduino] Conectado e pronto.")
        return

    if linha.startswith("ON:"):
        partes = linha.split(":")
        if len(partes) != 3:
            print(f"[AVISO] Linha ON malformada: {linha}")
            return
        _, posicao_str, uid = partes
        posicao = int(posicao_str)

        if uid in UID_PARA_EFEITO:
            musica, arquivo = UID_PARA_EFEITO[uid]
            player.iniciar_efeito(posicao, musica, arquivo)
            _tipo_ativo_por_posicao[posicao] = "efeito"
            return

        if uid in UID_PARA_MUSICA:
            instrumento = INSTRUMENTO_POR_POSICAO.get(posicao)
            if instrumento is None:
                print(f"[AVISO] Posição {posicao} sem instrumento mapeado.")
                return
            musica = UID_PARA_MUSICA[uid]
            player.colocar_ficha(posicao, musica, instrumento)
            _tipo_ativo_por_posicao[posicao] = "normal"
            return

        print(f"[AVISO] UID desconhecido: {uid} — cadastre em UID_PARA_MUSICA ou UID_PARA_EFEITO.")

    elif linha.startswith("OFF:"):
        partes = linha.split(":")
        if len(partes) != 2:
            print(f"[AVISO] Linha OFF malformada: {linha}")
            return
        posicao = int(partes[1])

        tipo = _tipo_ativo_por_posicao.pop(posicao, None)
        if tipo == "efeito":
            player.parar_efeito(posicao)
        else:
            player.retirar_ficha(posicao)

    else:
        print(f"[Arduino] {linha}")


def main():
    print(f"Conectando na porta {PORTA_SERIAL} ({BAUD_RATE} baud)...")
    try:
        arduino = serial.Serial(PORTA_SERIAL, BAUD_RATE, timeout=1)
    except serial.SerialException as e:
        print(f"[ERRO] Não consegui abrir a porta serial: {e}")
        print("Confira a porta e se o Monitor Serial da Arduino IDE está fechado.")
        sys.exit(1)

    time.sleep(2)

    print("Iniciando motor de áudio (stems carregam sob demanda)...")
    player = RockMachinePlayer(PASTA_STEMS, BPM_ALVO, COMPASSOS_POR_LOOP, TEMPOS_POR_COMPASSO)

    with player.iniciar_stream():
        print("\n=== RockMachine rodando — escutando o Arduino ===")
        print("Posições: 0=bateria, 1=vocal, 2=outros (baixo ignorado)")
        print("Ctrl+C para sair.\n")

        try:
            while True:
                linha = arduino.readline().decode(errors="ignore")
                processar_linha(linha, player)
        except KeyboardInterrupt:
            print("\nEncerrando...")
        finally:
            arduino.close()


if __name__ == "__main__":
    main()
