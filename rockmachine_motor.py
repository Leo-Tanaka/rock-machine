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

Gravação: em vez de capturar o áudio real que sai pela placa de som
(sujeito a timing de hardware, underflow, etc.), a "gravação" registra
só os EVENTOS (qual ficha, em qual posição, em que instante) enquanto a
sessão acontece ao vivo. Depois, a reconstrução do WAV final é feita
tocando esses eventos de volta matematicamente — mistura os stems já
carregados, sem depender da placa de som, então nunca perde qualidade
nem timing. A reprodução ao vivo (pros alto-falantes) continua rodando
normalmente e não é afetada por isso.

Os stems são carregados sob demanda (lazy loading) na primeira vez que
são usados, e ficam em cache depois disso.

Todo áudio é convertido automaticamente pra 44100 Hz e estéreo,
independente do arquivo original ser mono/estéreo ou 22050/44100 Hz.

Requisitos: pip install sounddevice soundfile numpy
"""

import sys
import threading
import time
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

        self._gravando = False
        self._tempo_inicio_gravacao = None
        self._eventos_gravacao = []
        self._estado_inicial_gravacao = ({}, {})

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
            if self._gravando:
                self._registrar_evento("colocar", posicao_id, musica, stem)
        print(f"[ON]  posição {posicao_id}: {musica} / {stem}")

    def retirar_ficha(self, posicao_id):
        with self.lock:
            removida = self.posicoes.pop(posicao_id, None)
            if removida and self._gravando:
                self._registrar_evento("retirar", posicao_id, None, None)
        if removida:
            print(f"[OFF] posição {posicao_id}: {removida[0]} / {removida[1]}")

    def iniciar_efeito(self, posicao_id, musica, arquivo):
        y = self._obter_efeito(musica, arquivo)
        if y is None:
            print(f"[AVISO] Não consegui carregar efeito {musica} / {arquivo} — ignorado.")
            return
        with self.lock:
            self.efeitos_ativos[posicao_id] = {"chave": (musica, arquivo), "leitura": 0}
            if self._gravando:
                self._registrar_evento("efeito_on", posicao_id, musica, arquivo)
        print(f"[ON-EFEITO] posição {posicao_id}: {musica} / {arquivo}")

    def parar_efeito(self, posicao_id):
        with self.lock:
            removido = self.efeitos_ativos.pop(posicao_id, None)
            if removido and self._gravando:
                self._registrar_evento("efeito_off", posicao_id, None, None)
        if removido:
            musica, arquivo = removido["chave"]
            print(f"[OFF-EFEITO] posição {posicao_id}: {musica} / {arquivo}")

    def definir_bpm_atual(self, novo_bpm):
        with self.lock:
            self.bpm_atual = max(20.0, float(novo_bpm))

    def _registrar_evento(self, tipo, posicao_id, musica, dado):
        tempo_relativo = time.time() - self._tempo_inicio_gravacao
        self._eventos_gravacao.append((tempo_relativo, tipo, posicao_id, musica, dado))

    def iniciar_gravacao(self):
        """Começa a registrar eventos a partir de agora. Guarda o estado
        atual (fichas já colocadas antes de começar) como ponto de
        partida da reconstrução."""
        with self.lock:
            self._estado_inicial_gravacao = (dict(self.posicoes), {})
            self._eventos_gravacao = []
            self._tempo_inicio_gravacao = time.time()
            self._gravando = True
        print("[GRAVAÇÃO] Iniciada (registrando eventos)")

    def parar_gravacao_e_salvar(self, caminho_saida, duracao_segundos):
        """Para de registrar e reconstrói o áudio inteiro a partir do
        roteiro de eventos — não depende de captura de áudio real."""
        with self.lock:
            self._gravando = False
            eventos = list(self._eventos_gravacao)
            estado_inicial = self._estado_inicial_gravacao
            self._eventos_gravacao = []

        if caminho_saida is None:
            return None

        audio_final = self._reconstruir_audio(eventos, estado_inicial, duracao_segundos)
        sf.write(str(caminho_saida), audio_final, self.sr)
        print(f"[GRAVAÇÃO] Reconstruída e salva em {caminho_saida} ({duracao_segundos:.1f}s)")
        return caminho_saida

    def _reconstruir_audio(self, eventos, estado_inicial, duracao_total_segundos):
        """Reproduz o roteiro de eventos matematicamente, num estado
        totalmente separado do que está tocando ao vivo."""
        ativos_offline = dict(estado_inicial[0])
        efeitos_offline = {k: dict(v) for k, v in estado_inicial[1].items()}

        total_amostras = int(round(duracao_total_segundos * self.sr))
        posicao_leitura_offline = 0.0
        pedacos = []
        amostras_geradas = 0

        eventos_ordenados = sorted(eventos, key=lambda e: e[0])
        indice_evento = 0
        tempo_atual = 0.0

        while amostras_geradas < total_amostras:
            if indice_evento < len(eventos_ordenados):
                proximo_tempo = eventos_ordenados[indice_evento][0]
            else:
                proximo_tempo = duracao_total_segundos

            duracao_trecho = max(0.0, min(proximo_tempo, duracao_total_segundos) - tempo_atual)
            n_frames_trecho = int(round(duracao_trecho * self.sr))
            n_frames_trecho = min(n_frames_trecho, total_amostras - amostras_geradas)

            if n_frames_trecho > 0:
                bloco, posicao_leitura_offline, efeitos_offline = self._misturar_trecho(
                    n_frames_trecho, ativos_offline, efeitos_offline, posicao_leitura_offline
                )
                pedacos.append(bloco)
                amostras_geradas += n_frames_trecho

            tempo_atual = proximo_tempo

            if indice_evento < len(eventos_ordenados) and tempo_atual >= eventos_ordenados[indice_evento][0]:
                _, tipo, posicao_id, musica, dado = eventos_ordenados[indice_evento]

                if tipo == "colocar":
                    ativos_offline[posicao_id] = (musica, dado)
                elif tipo == "retirar":
                    ativos_offline.pop(posicao_id, None)
                elif tipo == "efeito_on":
                    efeitos_offline[posicao_id] = {"chave": (musica, dado), "leitura": 0}
                elif tipo == "efeito_off":
                    efeitos_offline.pop(posicao_id, None)

                indice_evento += 1

            if tempo_atual >= duracao_total_segundos and amostras_geradas >= total_amostras:
                break

        if not pedacos:
            return np.zeros((total_amostras, 2), dtype=np.float32)

        audio_completo = np.concatenate(pedacos, axis=0)
        return audio_completo[:total_amostras]

    def _misturar_trecho(self, n_frames, ativos_dict, efeitos_dict, posicao_leitura):
        """Função pura: recebe um estado explícito e devolve o áudio e o
        estado seguinte. Não toca em self.posicoes/self.efeitos_ativos —
        por isso serve tanto pro áudio ao vivo quanto pra reconstrução
        offline, sem um interferir no outro."""
        bloco = np.zeros((n_frames, 2), dtype=np.float32)

        ativos = list(ativos_dict.values())
        passo = self.bpm_atual / self.bpm_render
        posicoes_float = (posicao_leitura + passo * np.arange(n_frames)) % self.loop_len_samples

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

        nova_posicao_leitura = (posicao_leitura + passo * n_frames) % self.loop_len_samples

        novos_efeitos = {}
        for posicao_id, estado in efeitos_dict.items():
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
            if nova_leitura < len(y):
                novos_efeitos[posicao_id] = {"chave": chave, "leitura": nova_leitura}

        pico = np.max(np.abs(bloco))
        if pico > 1.0:
            bloco = bloco / pico * 0.98

        return bloco, nova_posicao_leitura, novos_efeitos

    def gerar_bloco(self, n_frames):
        with self.lock:
            ativos_dict = dict(self.posicoes)
            efeitos_dict = dict(self.efeitos_ativos)

        bloco, nova_posicao_leitura, novos_efeitos = self._misturar_trecho(
            n_frames, ativos_dict, efeitos_dict, self.posicao_leitura
        )

        self.posicao_leitura = nova_posicao_leitura

        with self.lock:
            terminados = set(self.efeitos_ativos.keys()) - set(novos_efeitos.keys())
            for pid in list(self.efeitos_ativos.keys()):
                if pid in novos_efeitos:
                    self.efeitos_ativos[pid] = novos_efeitos[pid]
                elif pid in terminados:
                    del self.efeitos_ativos[pid]

        for posicao_id in terminados:
            print(f"[EFEITO TERMINOU] posição {posicao_id}")

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
