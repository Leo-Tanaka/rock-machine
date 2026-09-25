"""
RockMachine - Motor de áudio (posições fixas + efeitos imediatos)
=====================================================================
Modelo:
    - Cada POSIÇÃO física (leitor RFID) tem um instrumento fixo:
      0 = bateria (drums), 1 = vocal (vocals), 2 = outros (other).
      Baixo é ignorado — nenhuma posição toca "bass".
    - Cada FICHA (UID) representa uma música. Colocar a ficha numa
      posição toca o stem daquele instrumento, daquela música, em loop
      sincronizado com as outras posições.
    - Algumas fichas são "efeitos imediatos" — não entram no loop
      sincronizado; começam na hora que a ficha é colocada, e páram
      na hora que é retirada (ou quando o áudio termina naturalmente,
      o que vier primeiro).

Os stems são carregados sob demanda (lazy loading) na primeira vez que
são usados, e ficam em cache depois disso.

Todo áudio é convertido automaticamente pra 44100 Hz e estéreo,
independente do arquivo original ser mono/estéreo ou 22050/44100 Hz.

Requisitos: pip install sounddevice soundfile numpy
"""

import sys
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

# ============ CONFIGURAÇÕES ============

BPM_ALVO = 117
COMPASSOS_POR_LOOP = 16
TEMPOS_POR_COMPASSO = 4

PASTA_STEMS = "stems_prontos"

SAMPLE_RATE_ALVO = 44100

# ======================================================


def calcular_duracao_loop_segundos(bpm, compassos, tempos_por_compasso):
    duracao_um_tempo = 60.0 / bpm
    return duracao_um_tempo * tempos_por_compasso * compassos


def converter_sample_rate(audio, sr_original, sr_alvo):
    """Converte o sample rate por interpolação linear."""
    if sr_original == sr_alvo:
        return audio

    n_original = audio.shape[0]
    if n_original <= 1:
        return audio

    duracao = n_original / sr_original
    n_novo = int(round(duracao * sr_alvo))
    if n_novo <= 0:
        return audio

    eixo_original = np.linspace(0, 1, n_original)
    eixo_novo = np.linspace(0, 1, n_novo)
    canais = audio.shape[1]

    resultado = np.zeros((n_novo, canais), dtype=np.float32)
    for c in range(canais):
        resultado[:, c] = np.interp(eixo_novo, eixo_original, audio[:, c])

    return resultado


class RockMachinePlayer:
    def __init__(self, pasta_stems, bpm, compassos, tempos_por_compasso):
        self.pasta_stems = Path(pasta_stems)
        self.sr = SAMPLE_RATE_ALVO
        self.duracao_loop = calcular_duracao_loop_segundos(bpm, compassos, tempos_por_compasso)
        self.loop_len_samples = int(round(self.duracao_loop * self.sr))

        self._cache_sincronizados = {}
        self._cache_efeitos = {}

        self.posicoes = {}
        self.efeitos_ativos = {}
        self.lock = threading.Lock()

        self.bpm_render = bpm
        self.bpm_atual = bpm
        self.posicao_leitura = 0.0

        print(f"Duração do loop: {self.duracao_loop:.3f}s "
              f"({compassos} compassos a {bpm} BPM) | sample rate: {self.sr} Hz\n")

    def _carregar_arquivo(self, musica, nome_arquivo):
        caminho = self.pasta_stems / musica / f"{nome_arquivo}.wav"
        if not caminho.exists():
            print(f"[AVISO] Arquivo não encontrado: {caminho}")
            return None

        y, sr_original = sf.read(str(caminho), dtype="float32", always_2d=True)

        if sr_original != self.sr:
            y = converter_sample_rate(y, sr_original, self.sr)

        y = self._ajustar_canais(y)
        return y

    @staticmethod
    def _ajustar_canais(y):
        canais = y.shape[1]
        if canais == 1:
            return np.column_stack((y[:, 0], y[:, 0]))
        return y[:, :2]

    @staticmethod
    def _ajustar_tamanho_loop(y, tamanho_alvo):
        atual = y.shape[0]
        if atual == tamanho_alvo:
            return y
        if atual > tamanho_alvo:
            return y[:tamanho_alvo]
        preenchimento = np.zeros((tamanho_alvo - atual, y.shape[1]), dtype=np.float32)
        return np.vstack([y, preenchimento])

    def _obter_stem_sincronizado(self, musica, stem):
        chave = (musica, stem)
        if chave in self._cache_sincronizados:
            return self._cache_sincronizados[chave]

        print(f"Carregando (sincronizado): {musica} / {stem}")
        y = self._carregar_arquivo(musica, stem)
        if y is None:
            return None

        y = self._ajustar_tamanho_loop(y, self.loop_len_samples)
        self._cache_sincronizados[chave] = y
        print(f"  [OK] {musica} / {stem}\n")
        return y

    def _obter_efeito(self, musica, arquivo):
        chave = (musica, arquivo)
        if chave in self._cache_efeitos:
            return self._cache_efeitos[chave]

        print(f"Carregando (efeito): {musica} / {arquivo}")
        y = self._carregar_arquivo(musica, arquivo)
        if y is None:
            return None

        self._cache_efeitos[chave] = y
        print(f"  [OK] {musica} / {arquivo} ({len(y)/self.sr:.2f}s)\n")
        return y

    def colocar_ficha(self, posicao_id, musica, stem):
        y = self._obter_stem_sincronizado(musica, stem)
        if y is None:
            print(f"[AVISO] Não consegui carregar {musica} / {stem} — ficha ignorada.")
            return
        with self.lock:
            self.posicoes[posicao_id] = (musica, stem)
        print(f"[ON]  posição {posicao_id}: {musica} / {stem}")

    def retirar_ficha(self, posicao_id):
        with self.lock:
            removida = self.posicoes.pop(posicao_id, None)
        if removida:
            print(f"[OFF] posição {posicao_id}: {removida[0]} / {removida[1]}")

    def iniciar_efeito(self, posicao_id, musica, arquivo):
        y = self._obter_efeito(musica, arquivo)
        if y is None:
            print(f"[AVISO] Não consegui carregar efeito {musica} / {arquivo} — ignorado.")
            return
        with self.lock:
            self.efeitos_ativos[posicao_id] = {"chave": (musica, arquivo), "leitura": 0}
        print(f"[ON-EFEITO] posição {posicao_id}: {musica} / {arquivo}")

    def parar_efeito(self, posicao_id):
        with self.lock:
            removido = self.efeitos_ativos.pop(posicao_id, None)
        if removido:
            musica, arquivo = removido["chave"]
            print(f"[OFF-EFEITO] posição {posicao_id}: {musica} / {arquivo}")

    def definir_bpm_atual(self, novo_bpm):
        with self.lock:
            self.bpm_atual = max(20.0, float(novo_bpm))

    def gerar_bloco(self, n_frames):
        bloco = np.zeros((n_frames, 2), dtype=np.float32)

        with self.lock:
            ativos = list(self.posicoes.values())
            bpm_atual = self.bpm_atual
            efeitos = dict(self.efeitos_ativos)

        passo = bpm_atual / self.bpm_render
        posicoes_float = (self.posicao_leitura + passo * np.arange(n_frames)) % self.loop_len_samples

        if ativos:
            indices_base = np.floor(posicoes_float).astype(np.int64)
            indices_prox = (indices_base + 1) % self.loop_len_samples
            frac = (posicoes_float - indices_base).astype(np.float32)[:, None]

            for chave in ativos:
                y = self._cache_sincronizados.get(chave)
                if y is None:
                    continue
                amostra_atual = y[indices_base]
                amostra_prox = y[indices_prox]
                bloco += amostra_atual * (1.0 - frac) + amostra_prox * frac

        self.posicao_leitura = (self.posicao_leitura + passo * n_frames) % self.loop_len_samples

        efeitos_terminados = []
        for posicao_id, estado in efeitos.items():
            chave = estado["chave"]
            y = self._cache_efeitos.get(chave)
            if y is None:
                continue

            inicio = estado["leitura"]
            fim = min(inicio + n_frames, len(y))
            quantidade = fim - inicio

            if quantidade > 0:
                bloco[:quantidade] += y[inicio:fim]

            nova_leitura = inicio + n_frames
            if nova_leitura >= len(y):
                efeitos_terminados.append(posicao_id)
            else:
                with self.lock:
                    if posicao_id in self.efeitos_ativos:
                        self.efeitos_ativos[posicao_id]["leitura"] = nova_leitura

        for posicao_id in efeitos_terminados:
            with self.lock:
                removido = self.efeitos_ativos.pop(posicao_id, None)
            if removido:
                musica, arquivo = removido["chave"]
                print(f"[EFEITO TERMINOU] posição {posicao_id}: {musica} / {arquivo}")

        pico = np.max(np.abs(bloco))
        if pico > 1.0:
            bloco = bloco / pico * 0.98

        return bloco

    def iniciar_stream(self):
        def callback(outdata, frames, time_info, status):
            if status:
                print(status, file=sys.stderr)
            outdata[:] = self.gerar_bloco(frames)

        return sd.OutputStream(
            samplerate=self.sr,
            channels=2,
            callback=callback,
            dtype="float32",
            blocksize=2048,
            latency="high",
        )
