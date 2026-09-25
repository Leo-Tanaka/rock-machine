"""
RockMachine - Player em tempo real com RFID
============================================

Sistema:

RFID
  ↓
RC522
  ↓
Arduino
  ↓
USB Serial
  ↓
Python
  ↓
Áudio

Funcionamento:

- Drums / Bass / Vocals / Other:
    sincronizados no loop musical.

- Efeitos especiais:
    começam imediatamente quando a ficha é ativada.

Compatibilidade:
- WAV 22050 Hz
- WAV 44100 Hz
- WAV mono
- WAV stereo

Todos os áudios são convertidos internamente para 44100 Hz.
"""


# ======================================================
# IMPORTS
# ======================================================

import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
import serial


# ======================================================
# CONFIGURAÇÕES
# ======================================================

BPM_ALVO = 117

COMPASSOS_POR_LOOP = 16

TEMPOS_POR_COMPASSO = 4

PASTA_STEMS = "stems_prontos"


# ======================================================
# ARDUINO
# ======================================================

PORTA_SERIAL = "COM5"

BAUD_RATE = 115200


# ======================================================
# ÁUDIO
# ======================================================

# Todos os arquivos serão convertidos
# automaticamente para 44100 Hz.

SAMPLE_RATE_ALVO = 44100


# ======================================================
# STEMS QUE FUNCIONAM COMO EFEITOS
# ======================================================
#
# Esses stems não ficam presos ao loop.
#
# Quando o RFID é ativado:
#
#     começa imediatamente.
#
# Quando o RFID é desativado:
#
#     para.
#
# ======================================================

EFEITOS_IMEDIATOS = {

    "gemido-whatsapp",

}


# ======================================================
# MAPA RFID
# ======================================================

MAPA_RFID = {

    # ==================================================
    # ANOTHER ONE BITES THE DUST
    # ==================================================

    "73660EF7": (
        "Smells Like Teen Spirit",
        "drums"
    ),

    "41CA4B73": (
        "Tempos Modernos",
        "drums"
    ),

    "F9376A48": (
        "Billie Jean",
        "vocals"
    ),

    "1AD3781A": (
        "Smells Like Teen Spirit",
        "other"
    ),


    # ==================================================
    # GEMIDO WHATSAPP
    # ==================================================

    "4BA49079": (
        "Another One Bites the Dust",
        "gemido-whatsapp"
    ),


    # ==================================================
    # ESPRESSO
    # ==================================================

    "2A634C73": (
        "Tempos Modernos",
        "vocals"
    ),

    "21615573": (
        "Payphone",
        "other"
    ),

    "UID_ESPRESSO_VOCALS": (
        "Espresso",
        "vocals"
    ),

    "86AF4183": (
        "In the End",
        "other"
    ),


    # ==================================================
    # LEVITATING
    # ==================================================

    "UID_LEVITATING_DRUMS": (
        "Levitating",
        "drums"
    ),

    "": (
        "Levitating",
        "bass"
    ),

    "UID_LEVITATING_VOCALS": (
        "Levitating",
        "vocals"
    ),

    "UID_LEVITATING_OTHER": (
        "Levitating",
        "other"
    ),
}


# ======================================================
# DURAÇÃO DO LOOP
# ======================================================

def calcular_duracao_loop_segundos(
    bpm,
    compassos,
    tempos_por_compasso
):

    duracao_um_tempo = 60.0 / bpm

    return (
        duracao_um_tempo
        * tempos_por_compasso
        * compassos
    )


# ======================================================
# CONVERTER SAMPLE RATE
# ======================================================

def converter_sample_rate(
    audio,
    sample_rate_original,
    sample_rate_alvo
):
    """
    Converte o sample rate utilizando interpolação linear.
    """

    if sample_rate_original == sample_rate_alvo:

        return audio


    quantidade_original = audio.shape[0]


    if quantidade_original <= 1:

        return audio


    duracao = (
        quantidade_original
        / sample_rate_original
    )


    quantidade_nova = int(
        round(
            duracao
            * sample_rate_alvo
        )
    )


    if quantidade_nova <= 0:

        return audio


    eixo_original = np.linspace(
        0,
        1,
        quantidade_original
    )


    eixo_novo = np.linspace(
        0,
        1,
        quantidade_nova
    )


    canais = audio.shape[1]


    resultado = np.zeros(
        (
            quantidade_nova,
            canais
        ),
        dtype=np.float32
    )


    for canal in range(canais):

        resultado[:, canal] = np.interp(

            eixo_novo,

            eixo_original,

            audio[:, canal]

        )


    return resultado


# ======================================================
# PLAYER
# ======================================================

class RockMachinePlayer:

    def __init__(
        self,
        pasta_stems,
        bpm,
        compassos,
        tempos_por_compasso
    ):

        # ----------------------------------------------
        # SAMPLE RATE
        # ----------------------------------------------

        self.sr = SAMPLE_RATE_ALVO


        # ----------------------------------------------
        # DURAÇÃO DO LOOP
        # ----------------------------------------------

        self.duracao_loop = (

            calcular_duracao_loop_segundos(

                bpm,

                compassos,

                tempos_por_compasso

            )

        )


        # ----------------------------------------------
        # SAMPLES DO LOOP
        # ----------------------------------------------

        self.loop_len_samples = int(

            round(

                self.duracao_loop
                * self.sr

            )

        )


        # ----------------------------------------------
        # STEMS
        # ----------------------------------------------
        #
        # (musica, stem) -> áudio
        #

        self.stems = {}


        # ----------------------------------------------
        # STEMS MUSICAIS ATIVOS
        # ----------------------------------------------

        self.ativos = set()


        # ----------------------------------------------
        # EFEITOS ATIVOS
        # ----------------------------------------------

        self.efeitos_ativos = {}


        # ----------------------------------------------
        # LOCK
        # ----------------------------------------------

        self.lock = threading.Lock()


        # ----------------------------------------------
        # RELÓGIO DO LOOP
        # ----------------------------------------------

        self.sample_counter = 0


        # ----------------------------------------------
        # CARREGAR
        # ----------------------------------------------

        self._carregar_stems(
            pasta_stems
        )


    # ==================================================
    # CARREGAR STEMS
    # ==================================================

    def _carregar_stems(
        self,
        pasta_stems
    ):

        print("=" * 60)

        print(
            "CONFIGURAÇÃO DO ÁUDIO"
        )

        print("=" * 60)

        print()

        print(
            f"Sample rate alvo: "
            f"{self.sr} Hz"
        )

        print(
            f"Duração do loop: "
            f"{self.duracao_loop:.3f} segundos"
        )

        print(
            f"Loop: "
            f"{COMPASSOS_POR_LOOP} compassos"
        )

        print(
            f"BPM: "
            f"{BPM_ALVO}"
        )

        print(
            f"Samples por loop: "
            f"{self.loop_len_samples}"
        )

        print()

        print(
            "Carregando stems..."
        )

        print()


        stems_necessarios = set(
            MAPA_RFID.values()
        )


        # ==================================================
        # CARREGAMENTO
        # ==================================================

        for musica, stem_nome in stems_necessarios:

            caminho = (

                Path(pasta_stems)

                / musica

                / f"{stem_nome}.wav"

            )


            # ----------------------------------------------
            # ARQUIVO NÃO EXISTE
            # ----------------------------------------------

            if not caminho.exists():

                print(
                    "[AVISO] Stem não encontrado:"
                )

                print(
                    f"  {caminho}"
                )

                print()

                continue


            print(
                f"Carregando: "
                f"{musica} / {stem_nome}"
            )


            try:

                # ------------------------------------------
                # LER WAV
                # ------------------------------------------

                y, sr_original = sf.read(

                    str(caminho),

                    dtype="float32",

                    always_2d=True

                )


                print(
                    f"  Sample rate original: "
                    f"{sr_original} Hz"
                )


                print(
                    f"  Canais: "
                    f"{y.shape[1]}"
                )


                # ------------------------------------------
                # SAMPLE RATE
                # ------------------------------------------

                if sr_original != self.sr:

                    print(
                        f"  Convertendo "
                        f"{sr_original} Hz "
                        f"-> "
                        f"{self.sr} Hz"
                    )


                    y = converter_sample_rate(

                        y,

                        sr_original,

                        self.sr

                    )


                    print(
                        "  [OK] Conversão concluída"
                    )


                # ------------------------------------------
                # GARANTIR STEREO
                # ------------------------------------------

                y = self._ajustar_canais(
                    y
                )


                # ------------------------------------------
                # EFEITO
                # ------------------------------------------

                if stem_nome in EFEITOS_IMEDIATOS:

                    # Não precisa ter exatamente
                    # o tamanho do loop.

                    print(
                        "  Tipo: EFEITO IMEDIATO"
                    )


                # ------------------------------------------
                # STEM MUSICAL
                # ------------------------------------------

                else:

                    print(
                        "  Tipo: STEM SINCRONIZADO"
                    )


                    y = self._ajustar_tamanho_loop(

                        y,

                        self.loop_len_samples

                    )


                # ------------------------------------------
                # SALVAR
                # ------------------------------------------

                chave = (

                    musica,

                    stem_nome

                )


                self.stems[chave] = y


                # ------------------------------------------
                # DEBUG
                # ------------------------------------------

                pico = np.max(
                    np.abs(y)
                )


                duracao = (
                    len(y)
                    / self.sr
                )


                print(
                    f"  Duração: "
                    f"{duracao:.3f} segundos"
                )

                print(
                    f"  Pico: "
                    f"{pico:.6f}"
                )

                print(
                    f"  [OK] "
                    f"{musica} / {stem_nome}"
                )

                print()


            except Exception as erro:

                print(
                    "[ERRO] Falha ao carregar:"
                )

                print(
                    caminho
                )

                print(
                    f"Erro: {erro}"
                )

                print()


        # ==================================================
        # RESULTADO
        # ==================================================

        print("=" * 60)

        print(
            f"Total de stems carregados: "
            f"{len(self.stems)}"
        )

        print(
            f"Sample rate final: "
            f"{self.sr} Hz"
        )

        print("=" * 60)

        print()


        if not self.stems:

            raise RuntimeError(

                "Nenhum stem foi carregado.\n"

                f"Confira a pasta: {pasta_stems}"

            )


    # ==================================================
    # AJUSTAR CANAIS
    # ==================================================

    @staticmethod
    def _ajustar_canais(
        y
    ):

        canais = y.shape[1]


        # ----------------------------------------------
        # MONO
        # ----------------------------------------------

        if canais == 1:

            return np.column_stack(

                (

                    y[:, 0],

                    y[:, 0]

                )

            )


        # ----------------------------------------------
        # STEREO
        # ----------------------------------------------

        if canais >= 2:

            return y[:, :2]


        raise ValueError(
            "Áudio sem canais válidos."
        )


    # ==================================================
    # AJUSTAR LOOP
    # ==================================================

    @staticmethod
    def _ajustar_tamanho_loop(
        y,
        tamanho_alvo
    ):

        atual = y.shape[0]


        # ----------------------------------------------
        # CORRETO
        # ----------------------------------------------

        if atual == tamanho_alvo:

            return y


        # ----------------------------------------------
        # MAIOR
        # ----------------------------------------------

        if atual > tamanho_alvo:

            return y[
                :tamanho_alvo
            ]


        # ----------------------------------------------
        # MENOR
        # ----------------------------------------------

        preenchimento = np.zeros(

            (

                tamanho_alvo - atual,

                y.shape[1]

            ),

            dtype=np.float32

        )


        return np.vstack(

            [

                y,

                preenchimento

            ]

        )


    # ==================================================
    # ATIVAR / DESATIVAR
    # ==================================================

    def alternar(
        self,
        musica,
        stem
    ):

        chave = (

            musica,

            stem

        )


        # ----------------------------------------------
        # VERIFICAR EXISTÊNCIA
        # ----------------------------------------------

        if chave not in self.stems:

            print()

            print(
                "[ERRO] Stem não carregado:"
            )

            print(
                f"{musica} / {stem}"
            )

            return


        # ==================================================
        # EFEITO IMEDIATO
        # ==================================================

        if stem in EFEITOS_IMEDIATOS:

            with self.lock:

                if chave in self.efeitos_ativos:

                    # ----------------------------------
                    # OFF
                    # ----------------------------------

                    del self.efeitos_ativos[
                        chave
                    ]


                    print()

                    print(
                        "[OFF - EFEITO]"
                    )

                    print(
                        f"{musica} / {stem}"
                    )


                else:

                    # ----------------------------------
                    # ON
                    # ----------------------------------

                    self.efeitos_ativos[
                        chave
                    ] = 0


                    print()

                    print(
                        "[ON - EFEITO]"
                    )

                    print(
                        f"{musica} / {stem}"
                    )

                    print(
                        "Efeito começará imediatamente."
                    )


            return


        # ==================================================
        # STEM NORMAL
        # ==================================================

        with self.lock:

            if chave in self.ativos:

                # --------------------------------------
                # OFF
                # --------------------------------------

                self.ativos.discard(
                    chave
                )


                print()

                print(
                    "[OFF]"
                )

                print(
                    f"{musica} / {stem}"
                )


            else:

                # --------------------------------------
                # ON
                # --------------------------------------

                self.ativos.add(
                    chave
                )


                print()

                print(
                    "[ON]"
                )

                print(
                    f"{musica} / {stem}"
                )


    # ==================================================
    # GERAR BLOCO
    # ==================================================

    def gerar_bloco(
        self,
        n_frames
    ):

        # ----------------------------------------------
        # BLOCO STEREO
        # ----------------------------------------------

        bloco = np.zeros(

            (

                n_frames,

                2

            ),

            dtype=np.float32

        )


        # ----------------------------------------------
        # COPIAR ESTADOS
        # ----------------------------------------------

        with self.lock:

            ativos = list(
                self.ativos
            )

            efeitos = dict(
                self.efeitos_ativos
            )


        # ==================================================
        # STEMS SINCRONIZADOS
        # ==================================================

        if ativos:

            indices = (

                np.arange(

                    self.sample_counter,

                    self.sample_counter
                    + n_frames

                )

                %

                self.loop_len_samples

            )


            for chave in ativos:

                y = self.stems.get(
                    chave
                )


                if y is None:

                    continue


                trecho = y[
                    indices
                ]


                bloco += trecho


        # ==================================================
        # EFEITOS IMEDIATOS
        # ==================================================

        if efeitos:

            for chave, posicao in efeitos.items():

                y = self.stems.get(
                    chave
                )


                if y is None:

                    continue


                inicio = posicao


                fim = min(

                    inicio + n_frames,

                    len(y)

                )


                quantidade = (
                    fim - inicio
                )


                if quantidade > 0:

                    bloco[
                        :quantidade
                    ] += y[
                        inicio:fim
                    ]


                # Atualiza posição
                # do efeito

                with self.lock:

                    if chave in self.efeitos_ativos:

                        self.efeitos_ativos[
                            chave
                        ] += n_frames


                        # ----------------------------------
                        # EFEITO TERMINOU
                        # ----------------------------------

                        if (

                            self.efeitos_ativos[
                                chave
                            ]

                            >=

                            len(y)

                        ):

                            del self.efeitos_ativos[
                                chave
                            ]


                            print()

                            print(
                                "[EFEITO TERMINOU]"
                            )

                            print(
                                f"{chave[0]} / "
                                f"{chave[1]}"
                            )


        # ==================================================
        # EVITAR CLIPPING
        # ==================================================

        pico = np.max(
            np.abs(bloco)
        )


        if pico > 1.0:

            bloco = (

                bloco

                /

                pico

                *

                0.98

            )


        # ==================================================
        # ATUALIZAR RELÓGIO
        # ==================================================

        self.sample_counter += n_frames


        return bloco


# ======================================================
# MONITORAR RFID
# ======================================================

def monitorar_rfid(
    player,
    ser
):

    print()

    print("=" * 60)

    print(
        "RFID PRONTO"
    )

    print(
        "Aproxime uma ficha..."
    )

    print(
        "Ctrl+C para encerrar."
    )

    print("=" * 60)

    print()


    # ----------------------------------------------
    # DEBOUNCE
    # ----------------------------------------------

    ultimo_uid = ""

    ultima_leitura = 0

    TEMPO_DEBOUNCE = 1.5


    while True:

        try:

            if ser.in_waiting > 0:

                uid = (

                    ser.readline()

                    .decode(

                        "utf-8",

                        errors="ignore"

                    )

                    .strip()

                    .upper()

                )


                if not uid:

                    continue


                agora = time.time()


                # --------------------------------------
                # IGNORA REPETIÇÃO
                # --------------------------------------

                if (

                    uid == ultimo_uid

                    and

                    agora - ultima_leitura
                    < TEMPO_DEBOUNCE

                ):

                    continue


                ultimo_uid = uid

                ultima_leitura = agora


                print()

                print(
                    f"RFID DETECTADO: {uid}"
                )


                # ==================================================
                # CADASTRADO
                # ==================================================

                if uid in MAPA_RFID:

                    musica, stem = (

                        MAPA_RFID[uid]

                    )


                    print(

                        f"FICHA: "
                        f"{musica}"
                        f" / "
                        f"{stem}"

                    )


                    player.alternar(

                        musica,

                        stem

                    )


                # ==================================================
                # NÃO CADASTRADO
                # ==================================================

                else:

                    print()

                    print(
                        "[AVISO]"
                    )

                    print(
                        "UID ainda não cadastrado."
                    )

                    print(
                        "Adicione ao MAPA_RFID:"
                    )

                    print()

                    print(

                        f'"{uid}": '

                        '("NOME DA MUSICA", '

                        '"NOME_DO_STEM"),'

                    )


            else:

                time.sleep(
                    0.005
                )


        except Exception as erro:

            print()

            print(
                f"[ERRO RFID] {erro}"
            )

            time.sleep(
                1
            )


# ======================================================
# RODAR SISTEMA
# ======================================================

def rodar_sistema(
    player
):

    print()

    print(
        "Conectando ao Arduino..."
    )


    # ==================================================
    # SERIAL
    # ==================================================

    try:

        ser = serial.Serial(

            PORTA_SERIAL,

            BAUD_RATE,

            timeout=1

        )


        # Arduino normalmente reinicia
        # ao abrir a porta serial.

        time.sleep(2)


    except Exception as erro:

        print()

        print(
            "ERRO AO CONECTAR NO ARDUINO"
        )

        print()

        print(
            f"Porta configurada: "
            f"{PORTA_SERIAL}"
        )

        print()

        print(
            "Verifique:"
        )

        print(
            "- Arduino conectado"
        )

        print(
            "- Porta COM correta"
        )

        print(
            "- Monitor Serial fechado"
        )

        print()

        print(
            f"Erro: {erro}"
        )

        return


    # ==================================================
    # THREAD RFID
    # ==================================================

    thread_rfid = threading.Thread(

        target=monitorar_rfid,

        args=(

            player,

            ser

        ),

        daemon=True

    )


    thread_rfid.start()


    # ==================================================
    # CALLBACK
    # ==================================================

    def callback(

        outdata,

        frames,

        time_info,

        status

    ):

        if status:

            print(

                status,

                file=sys.stderr

            )


        outdata[:] = (

            player.gerar_bloco(

                frames

            )

        )


    # ==================================================
    # ÁUDIO
    # ==================================================

    try:

        with sd.OutputStream(

            samplerate=player.sr,

            channels=2,

            callback=callback,

            dtype="float32"

        ):

            print()

            print("=" * 60)

            print(
                "ROCKMACHINE RODANDO"
            )

            print(
                "Áudio sincronizado + RFID ativo"
            )

            print(
                f"Sample rate: "
                f"{player.sr} Hz"
            )

            print()

            print(
                "STEMS:"
            )

            print(
                "Drums / Bass / Vocals / Other"
            )

            print(
                "→ sincronizados"
            )

            print()

            print(
                "EFEITOS:"
            )

            print(
                "→ começam imediatamente"
            )

            print()

            print(
                "Pressione Ctrl+C para sair"
            )

            print("=" * 60)

            print()


            # ------------------------------------------
            # LOOP PRINCIPAL
            # ------------------------------------------

            while True:

                time.sleep(
                    1
                )


    except KeyboardInterrupt:

        print()

        print(
            "Encerrando RockMachine..."
        )


    except Exception as erro:

        print()

        print(
            "[ERRO DE ÁUDIO]"
        )

        print(
            erro
        )


    finally:

        try:

            ser.close()

        except Exception:

            pass


# ======================================================
# MAIN
# ======================================================

def main():

    player = RockMachinePlayer(

        PASTA_STEMS,

        BPM_ALVO,

        COMPASSOS_POR_LOOP,

        TEMPOS_POR_COMPASSO

    )


    rodar_sistema(

        player

    )


# ======================================================
# EXECUTAR
# ======================================================

if __name__ == "__main__":

    main()