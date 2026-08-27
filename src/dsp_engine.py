from __future__ import annotations

import os
import warnings
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

warnings.filterwarnings("ignore", category=UserWarning)


class MotorDSP:
    def __init__(self, bpm_global: float, tom_global_id: int):
        self.bpm_global = float(bpm_global)
        self.tom_global_id = int(tom_global_id)

    @staticmethod
    def detectar_bpm(y: np.ndarray, sr: int) -> float:
        """Detecta o BPM (tempo) de um áudio usando librosa.

        Usa librosa.feature.tempo (API atual). Em versões antigas do librosa
        essa função se chamava librosa.beat.tempo, por isso o fallback abaixo.
        """
        try:
            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            if hasattr(librosa.feature, "tempo"):
                tempo = librosa.feature.tempo(onset_envelope=onset_env, sr=sr)
            else:
                tempo = librosa.beat.tempo(onset_envelope=onset_env, sr=sr)  # librosa antigo
            return float(tempo[0]) if isinstance(tempo, np.ndarray) else float(tempo)
        except Exception as e:
            print(f"Aviso: Falha na detecção de BPM: {e}. Retornando 120 como padrão.")
            return 120.0

    @staticmethod
    def detectar_inicio_do_groove(y: np.ndarray, sr: int) -> float:
        """Detecta em que segundo o primeiro beat forte acontece, para
        conseguirmos cortar silêncio/intro do início do stem. Sem isso, um
        stem com alguns segundos de silêncio no começo entra "atrasado" em
        relação aos outros ao tocar em loop sincronizado."""
        try:
            y_mono = librosa.to_mono(y) if y.ndim > 1 else y
            onset_env = librosa.onset.onset_strength(y=y_mono, sr=sr)
            onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, units="time")
            if len(onsets) == 0:
                return 0.0
            return float(onsets[0])
        except Exception as e:
            print(f"Aviso: Falha ao detectar início do groove: {e}. Usando o início do arquivo.")
            return 0.0

    @staticmethod
    def detectar_tom_fundamental(y: np.ndarray, sr: int) -> int:
        """Detecta o tom fundamental usando chroma e retorna o semitom (0-11)."""
        try:
            chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
            chroma_mean = np.mean(chroma, axis=1)
            tom_id = int(np.argmax(chroma_mean))
            return tom_id
        except Exception as e:
            print(f"Aviso: Falha na detecção de tom: {e}. Retornando 0 (C) como padrão.")
            return 0

    def processar_faixa(
        self,
        caminho_entrada: str | Path,
        caminho_saida: str | Path,
        bpm_original: float,
        tom_original_id: int,
    ) -> str:
        caminho_entrada = Path(caminho_entrada)
        caminho_saida = Path(caminho_saida)

        print(f"Processando: {caminho_entrada}...")

        # sr=None preserva a taxa original; mono=False mantém canais quando existirem.
        y, sr = librosa.load(caminho_entrada, sr=None, mono=False)

        y = self._cortar_silencio_inicial(y, sr)
        y = self._aplicar_time_stretch(y, float(bpm_original))
        y = self._aplicar_pitch_shift(y, sr, int(tom_original_id))

        caminho_saida.parent.mkdir(parents=True, exist_ok=True)
        sf.write(caminho_saida, y.T if y.ndim == 2 else y, sr)
        print(f"Concluído: {caminho_saida}\n")
        return str(caminho_saida)

    def _cortar_silencio_inicial(self, y: np.ndarray, sr: int) -> np.ndarray:
        """Corta qualquer silêncio/intro no início do stem, começando no
        primeiro beat forte detectado. Evita que uma música com intro
        silenciosa entre "atrasada" quando tocada em loop com outras."""
        ponto_corte = self.detectar_inicio_do_groove(y, sr)
        if ponto_corte <= 0:
            return y

        amostra_corte = int(ponto_corte * sr)
        print(f" -> Cortando silêncio/intro inicial: {ponto_corte:.2f}s")

        if y.ndim == 1:
            return y[amostra_corte:]
        return y[:, amostra_corte:]

    def _aplicar_time_stretch(self, y: np.ndarray, bpm_original: float) -> np.ndarray:
        if bpm_original <= 0:
            return y

        rate_tempo = self.bpm_global / bpm_original
        if rate_tempo == 1.0:
            return y

        print(f" -> Ajustando tempo: {bpm_original} BPM para {self.bpm_global} BPM")
        if y.ndim == 1:
            return librosa.effects.time_stretch(y, rate=rate_tempo)

        canais = [librosa.effects.time_stretch(canal, rate=rate_tempo) for canal in y]
        menor_tamanho = min(canal.shape[-1] for canal in canais)
        canais_alinhados = [canal[..., :menor_tamanho] for canal in canais]
        return np.vstack(canais_alinhados)

    def _aplicar_pitch_shift(self, y: np.ndarray, sr: int, tom_original_id: int) -> np.ndarray:
        passos_pitch = self.tom_global_id - tom_original_id
        if passos_pitch == 0:
            return y

        print(f" -> Ajustando tom: {passos_pitch} semitons")
        if y.ndim == 1:
            return librosa.effects.pitch_shift(y, sr=sr, n_steps=passos_pitch)

        canais = [librosa.effects.pitch_shift(canal, sr=sr, n_steps=passos_pitch) for canal in y]
        menor_tamanho = min(canal.shape[-1] for canal in canais)
        canais_alinhados = [canal[..., :menor_tamanho] for canal in canais]
        return np.vstack(canais_alinhados)