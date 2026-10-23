"""
RockMachine - Modo híbrido completo: RFID + teclado + gravação
======================================================================
Escuta os Arduinos normalmente (RFID controla as posições), deixa você
sobrepor qualquer posição pelo teclado (útil se um leitor falhar), e
grava/publica a sessão na playlist — tudo no mesmo programa.

PRIORIDADE ENTRE RFID E TECLADO:
    Quem agiu por último manda. Se o RFID colocar uma ficha na posição
    0 e depois você apertar uma tecla pra posição 0, o teclado vence
    (e vice-versa) — não precisa "desativar" o RFID antes.

TECLAS DE MÚSICA (pega automaticamente as 5 primeiras músicas
cadastradas em UID_PARA_MUSICA, do ponte_serial.py):
    Posição 0 (bateria):  1 / 2 / 3 / 4 / 5
    Posição 1 (vocal):    q / w / e / r / t
    Posição 2 (outros):   a / s / d / f / g
    (mesma tecla de novo = retirar aquela posição)

TECLAS DE GRAVAÇÃO:
    i -> pede nome da música e do autor, começa a gravar. Para e
         publica sozinho quando o tempo definido em COMPASSOS_GRAVACAO
         acabar.
    c -> cancela a gravação atual antes da hora (não salva, não publica)

    ESC -> sai do programa

Requisitos: pip install pyserial sounddevice soundfile numpy requests
IMPORTANTE: usa 'msvcrt' (exclusivo do Windows).
"""

import time
from pathlib import Path

from rockmachine_motor import (
    RockMachinePlayer,
    BPM_ALVO,
    COMPASSOS_POR_LOOP,
    TEMPOS_POR_COMPASSO,
    PASTA_STEMS,
    calcular_duracao_loop_segundos,
)
from ponte_serial import LeitoresSerial, PORTAS_SERIAIS, BAUD_RATE, UID_PARA_MUSICA, INSTRUMENTO_POR_POSICAO
from gravar_sessao import (
    pedir_dados_publicacao,
    nome_para_arquivo,
    publicar_musica,
    COMPASSOS_GRAVACAO,
    PASTA_GRAVACOES,
)

try:
    import msvcrt
    WINDOWS = True
except ImportError:
    WINDOWS = False

# ============ MONTA AS TECLAS DE MÚSICA AUTOMATICAMENTE ============

_musicas_distintas = list(dict.fromkeys(UID_PARA_MUSICA.values()))
MUSICAS_DEMO = _musicas_distintas[:5]

TECLAS_POR_POSICAO = {
    0: ["1", "2", "3", "4", "5"],
    1: ["q", "w", "e", "r", "t"],
    2: ["a", "s", "d", "f", "g"],
}

MAPA_TECLAS = {}
for _posicao, _teclas in TECLAS_POR_POSICAO.items():
    for _tecla, _musica in zip(_teclas, MUSICAS_DEMO):
        MAPA_TECLAS[_tecla] = (_posicao, _musica)

# Teclas de gravação — fora do grid de música, sem colisão
TECLA_INICIAR_GRAVACAO = "i"
TECLA_CANCELAR_GRAVACAO = "c"

# =====================================================================


def main():
    if not WINDOWS:
        print("AVISO: 'msvcrt' não disponível (isso não é Windows). Rode no Windows.")
        return

    if len(MUSICAS_DEMO) < 5:
        print(f"[AVISO] Só há {len(MUSICAS_DEMO)} música(s) cadastrada(s) — "
              f"o teclado vai ter menos teclas que o normal.")

    Path(PASTA_GRAVACOES).mkdir(exist_ok=True)

    duracao_gravacao_seg = calcular_duracao_loop_segundos(
        BPM_ALVO, COMPASSOS_GRAVACAO, TEMPOS_POR_COMPASSO
    )

    print(f"Conectando em {len(PORTAS_SERIAIS)} porta(s): {', '.join(PORTAS_SERIAIS)} ({BAUD_RATE} baud)...")
    leitores = LeitoresSerial(PORTAS_SERIAIS, BAUD_RATE)
    leitores.iniciar()

    print("Carregando motor de áudio...")
    player = RockMachinePlayer(PASTA_STEMS, BPM_ALVO, COMPASSOS_POR_LOOP, TEMPOS_POR_COMPASSO)

    gravando = False
    caminho_saida = None
    nome_musica = None
    nome_autor = None
    tempo_inicio = None

    with player.iniciar_stream():
        print("\n=== RockMachine — Modo híbrido (RFID + teclado + gravação) ===")
        print("O RFID continua funcionando nas 3 posições (1 Arduino por leitor).\n")
        print("Teclas de música (sobrepõe o RFID na hora):")
        for tecla, (posicao, musica) in MAPA_TECLAS.items():
            instrumento = INSTRUMENTO_POR_POSICAO[posicao]
            print(f"  '{tecla}' -> posição {posicao} ({instrumento}) = {musica}")
        print(f"\n  '{TECLA_INICIAR_GRAVACAO}' -> iniciar gravação "
              f"({duracao_gravacao_seg:.1f}s, publica sozinho no final)")
        print(f"  '{TECLA_CANCELAR_GRAVACAO}' -> cancelar gravação em andamento")
        print("\nMesma tecla de música de novo = retirar. ESC para sair.\n")

        try:
            while True:
                leitores.processar_pendentes(player)

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

                    if tecla == TECLA_INICIAR_GRAVACAO:
                        if gravando:
                            print("[AVISO] Já está gravando.")
                        else:
                            nome_musica, nome_autor = pedir_dados_publicacao()
                            caminho_saida = Path(PASTA_GRAVACOES) / nome_para_arquivo(nome_musica)
                            player.iniciar_gravacao()
                            tempo_inicio = time.time()
                            gravando = True
                            print(f"Gravando por {duracao_gravacao_seg:.1f}s — "
                                  f"pode interagir com RFID/teclado normalmente.")

                    elif tecla == TECLA_CANCELAR_GRAVACAO:
                        if not gravando:
                            print("[AVISO] Não está gravando.")
                        else:
                            player.parar_gravacao_e_salvar(None, duracao_gravacao_seg)
                            gravando = False
                            print("[GRAVAÇÃO] Cancelada, nada foi salvo nem publicado.")

                    elif tecla in MAPA_TECLAS:
                        posicao, musica = MAPA_TECLAS[tecla]
                        instrumento = INSTRUMENTO_POR_POSICAO[posicao]

                        ja_ativa = player.posicoes.get(posicao) == (musica, instrumento)
                        if ja_ativa:
                            player.retirar_ficha(posicao)
                        else:
                            player.colocar_ficha(posicao, musica, instrumento)

        except KeyboardInterrupt:
            print("\nEncerrando...")
        finally:
            leitores.fechar()


if __name__ == "__main__":
    main()