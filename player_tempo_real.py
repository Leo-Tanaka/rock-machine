"""
RockMachine - Player em tempo real (sincronizado por loop)
=============================================================
Simula o comportamento do hardware: cada tecla representa uma "ficha".
Apertar a tecla ativa/desativa aquele stem, tocando em loop, sempre em
fase com os outros stems que já estão tocando (mesmo BPM = mesmo tamanho
de loop = sincronização automática, sem precisar calcular ponto de início).

Como funciona a sincronização:
    Como todos os stems têm exatamente a mesma duração de loop (calculada
    a partir do BPM alvo e do número de compassos), a posição de leitura de
    QUALQUER stem em QUALQUER momento é: (amostra_atual_global % tamanho_do_loop)
    Isso significa que não importa quando você ativa um stem — ele sempre
    vai "cair" no lugar certo do compasso, porque todos compartilham o
    mesmo relógio global.

Teclas (mapeadas por música):
    Another One Bites the Dust:  1=bateria  2=baixo  3=vocal  4=outros
    Espresso:                    q=bateria  w=baixo  e=vocal  r=outros
    Levitating:                  a=bateria  s=baixo  d=vocal  f=outros
    ESC ou Ctrl+C para sair

Requisitos:
    pip install sounddevice soundfile numpy

IMPORTANTE: este script usa 'msvcrt', que é exclusivo do Windows (vocês
estão rodando no Windows, então tá tranquilo). Se um dia rodarem em
Mac/Linux, me avisem que adapto pra 'pynput' ou 'keyboard'.
"""

import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

try:
    import msvcrt
    WINDOWS = True
except ImportError:
    WINDOWS = False

# ============ CONFIGURAÇÕES ============

BPM_ALVO = 106
COMPASSOS_POR_LOOP = 8  # 8 compassos de 4 tempos é um tamanho de frase comum
TEMPOS_POR_COMPASSO = 4

PASTA_STEMS = "stems_prontos"

# Mapa de tecla -> (música, stem)
MAPA_TECLAS = {
    "1": ("Another One Bites the Dust", "drums"),
    "2": ("Another One Bites the Dust", "bass"),
    "3": ("Another One Bites the Dust", "vocals"),
    "4": ("Another One Bites the Dust", "other"),
    "q": ("Espresso", "drums"),
    "w": ("Espresso", "bass"),
    "e": ("Espresso", "vocals"),
    "r": ("Espresso", "other"),
    "a": ("Levitating", "drums"),
    "s": ("Levitating", "bass"),
    "d": ("Levitating", "vocals"),
    "f": ("Levitating", "other"),
}

# ======================================================


def calcular_duracao_loop_segundos(bpm, compassos, tempos_por_compasso):
    duracao_um_tempo = 60.0 / bpm
    return duracao_um_tempo * tempos_por_compasso * compassos


class RockMachinePlayer:
    def __init__(self, pasta_stems, bpm, compassos, tempos_por_compasso):
        self.sr = None
        self.duracao_loop = calcular_duracao_loop_segundos(bpm, compassos, tempos_por_compasso)
        self.loop_len_samples = None
        self.stems = {}  # tecla -> ndarray (float32, mono ou stereo) já cortado no tamanho do loop
        self.ativos = set()  # teclas atualmente tocando
        self.lock = threading.Lock()
        self.sample_counter = 0

        self._carregar_stems(pasta_stems)

    def _carregar_stems(self, pasta_stems):
        print(f"Duração do loop calculada: {self.duracao_loop:.3f}s "
              f"({COMPASSOS_POR_LOOP} compassos a {BPM_ALVO} BPM)\n")

        for tecla, (musica, stem_nome) in MAPA_TECLAS.items():
            caminho = Path(pasta_stems) / musica / f"{stem_nome}.wav"
            if not caminho.exists():
                print(f"  [AVISO] Não encontrado, tecla '{tecla}' ficará inativa: {caminho}")
                continue

            y, sr = sf.read(str(caminho), dtype="float32", always_2d=True)
            if self.sr is None:
                self.sr = sr
                self.loop_len_samples = int(self.duracao_loop * self.sr)
            elif sr != self.sr:
                print(f"  [AVISO] Sample rate diferente em {caminho} ({sr} != {self.sr}), pulando.")
                continue

            y = self._ajustar_tamanho_loop(y, self.loop_len_samples)
            self.stems[tecla] = y
            print(f"  [OK] '{tecla}' -> {musica} / {stem_nome}")

        if not self.stems:
            raise RuntimeError("Nenhum stem carregado. Confira o caminho de PASTA_STEMS.")

    @staticmethod
    def _ajustar_tamanho_loop(y, tamanho_alvo):
        """Corta ou preenche com silêncio para o stem ter exatamente o
        tamanho do loop (em amostras). Isso é o que garante o encaixe
        perfeito entre todos os stems, não importa quando são ativados."""
        atual = y.shape[0]
        if atual == tamanho_alvo:
            return y
        if atual > tamanho_alvo:
            return y[:tamanho_alvo]
        # Preenche com silêncio (padding) se o stem for mais curto que o loop
        preenchimento = np.zeros((tamanho_alvo - atual, y.shape[1]), dtype=y.dtype)
        return np.vstack([y, preenchimento])

    def alternar(self, tecla):
        if tecla not in self.stems:
            return
        with self.lock:
            if tecla in self.ativos:
                self.ativos.discard(tecla)
                print(f"[OFF] {MAPA_TECLAS[tecla][0]} / {MAPA_TECLAS[tecla][1]}")
            else:
                self.ativos.add(tecla)
                print(f"[ON]  {MAPA_TECLAS[tecla][0]} / {MAPA_TECLAS[tecla][1]}")

    def gerar_bloco(self, n_frames):
        """Gera o próximo bloco de áudio somando todos os stems ativos,
        cada um lido na posição correspondente ao relógio global (por isso
        tudo fica sempre sincronizado, mesmo ativando fora de ordem)."""
        n_canais = 2
        bloco = np.zeros((n_frames, n_canais), dtype=np.float32)

        with self.lock:
            ativos = list(self.ativos)

        if not ativos:
            self.sample_counter += n_frames
            return bloco

        # Índices (com wraparound) de onde ler no loop de cada stem neste bloco
        indices = (np.arange(self.sample_counter, self.sample_counter + n_frames)
                   % self.loop_len_samples)

        for tecla in ativos:
            y = self.stems[tecla]
            trecho = y[indices]  # (n_frames, canais_do_stem)
            if trecho.shape[1] == 1:
                bloco[:, 0] += trecho[:, 0]
                bloco[:, 1] += trecho[:, 0]
            else:
                bloco += trecho[:, :2]

        # Evita clipping quando muitos stems tocam juntos
        pico = np.max(np.abs(bloco))
        if pico > 1.0:
            bloco = bloco / pico * 0.98

        self.sample_counter += n_frames
        return bloco


def rodar_com_audio(player):
    def callback(outdata, frames, time_info, status):
        if status:
            print(status, file=sys.stderr)
        outdata[:] = player.gerar_bloco(frames)

    with sd.OutputStream(samplerate=player.sr, channels=2, callback=callback, dtype="float32"):
        print("\n=== RockMachine Player rodando ===")
        print("Teclas ativas:")
        for tecla, (musica, stem) in MAPA_TECLAS.items():
            marcador = "OK" if tecla in player.stems else "--"
            print(f"  [{marcador}] '{tecla}' -> {musica} / {stem}")
        print("\nAperte as teclas para ativar/desativar stems. ESC para sair.\n")

        if not WINDOWS:
            print("AVISO: 'msvcrt' não disponível (isso não é Windows).")
            print("Rode este script na máquina Windows de vocês.")
            return

        while True:
            tecla = msvcrt.getch().decode(errors="ignore").lower()
            if tecla == "\x1b":  # ESC
                print("\nEncerrando...")
                break
            player.alternar(tecla)


def main():
    player = RockMachinePlayer(PASTA_STEMS, BPM_ALVO, COMPASSOS_POR_LOOP, TEMPOS_POR_COMPASSO)
    rodar_com_audio(player)


if __name__ == "__main__":
    main()
