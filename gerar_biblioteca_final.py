"""
RockMachine - Gerar biblioteca final (tempo + tom + volume numa passada só)
==========================================================================
Pega os stems que o Demucs separou (drums, vocals, other - o baixo já foi
somado no "other") e entrega em stems_prontos/<música>/ tudo pronto pro
rockmachine_motor.py tocar:

    1. Acha onde o groove começa (ou usa o início que você marcou)
    2. Recorta EXATAMENTE N compassos (COMPASSOS_POR_LOOP)
    3. Muda tempo E tom numa passada só no rubberband (sem processar duas
       vezes = menos artefato). No vocal preserva o timbre (formantes)
    4. Ajusta o tamanho final pro loop exato do motor (sem deriva)
    5. Iguala o volume entre as músicas (mesmo ganho nos 3 stems da música,
       então o equilíbrio bateria/vocal/outros dela não muda)

Requisitos (Colab):
    !apt-get install -y rubberband-cli
    !pip install librosa soundfile numpy scipy

Como usar:
    1. Passe os clips (refrão cortado no Audacity) pelo Demucs, como antes
    2. Confira o dicionário MUSICAS abaixo (BPM e tom de cada música)
    3. python gerar_biblioteca_final.py
       (ou só algumas:  python gerar_biblioteca_final.py "Numb" "Get Lucky")
       (com teste de combinações:  python gerar_biblioteca_final.py --preview)
    4. Copie a pasta stems_prontos/ pro notebook onde roda o RockMachine
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

import librosa
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

# ============ CONFIGURAÇÕES - edite aqui ============

# TEM QUE SER IGUAL ao rockmachine_motor.py
BPM_ALVO = 117
COMPASSOS_POR_LOOP = 16
TEMPOS_POR_COMPASSO = 4
SAMPLE_RATE = 44100

# Tom alvo: todo mundo é levado pra esse tom (ou pro relativo dele)
TOM_ALVO = ("F#", "menor")

PASTA_STEMS_ORIGINAIS = "stems_separados/htdemucs"
PASTA_SAIDA = "stems_prontos"
STEMS = ["drums", "vocals", "other"]

# Volume: RMS-alvo da mistura de cada música e pico máximo dessa mistura.
# Pico baixo (0.5) dá folga pro motor somar até 3 músicas sem estourar.
RMS_ALVO_DB = -20.0
PICO_MAX_MISTURA = 0.5

# Para cada música:
#   bpm            BPM do trecho que você recortou (obrigatório)
#   tom, modo      tom original, ex: "F#" + "menor" ("maior" também vale).
#                  Use None se não souber: a música NÃO é transposta.
#   inicio_s       (opcional) segundo exato do tempo 1 do primeiro compasso.
#                  None = o script detecta o primeiro beat sozinho.
#   compassos_clip (opcional) quantos compassos tem o clip. Se for menor que
#                  COMPASSOS_POR_LOOP e dividir ele (ex: 8 pra 16), o clip é
#                  repetido pra preencher o loop. Padrão = COMPASSOS_POR_LOOP.
#   semitons       (opcional) força a transposição, ignorando tom/modo.
MUSICAS = {
    "Billie Jean": {"bpm": 117, "tom": "F#", "modo": "menor"},
    "Numb": {"bpm": 110, "tom": "G", "modo": "menor"},
    "Paparazzi": {"bpm": 115, "tom": "F", "modo": "maior"},
    "Tudo Que Ela Gosta De Escutar": {"bpm": 115, "tom": 'F', "modo": "menor"},
    "Get Lucky": {"bpm": 116, "tom": "B", "modo": "menor"},
}

# ====================================================

NOTAS = {
    "C": 0, "B#": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4,
    "FB": 4, "F": 5, "E#": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8,
    "A": 9, "A#": 10, "BB": 10, "B": 11, "CB": 11,
}


def classe_relativa_maior(tom, modo):
    """Músicas no tom relativo (F# menor e A maior) usam as mesmas notas, então
    comparamos sempre pela classe do relativo maior."""
    pc = NOTAS[tom.strip().upper()]
    return (pc + 3) % 12 if str(modo).lower().startswith("men") else pc


def calcular_semitons(tom, modo):
    if tom is None:
        return 0, False
    atual = classe_relativa_maior(tom, modo)
    alvo = classe_relativa_maior(*TOM_ALVO)
    delta = (alvo - atual) % 12
    if delta > 6:
        delta -= 12
    return delta, True


# ---------- rubberband ----------

def _rubberband_tem_fine():
    try:
        ajuda = subprocess.run(["rubberband", "--help"], capture_output=True, text=True)
        return "--fine" in (ajuda.stdout + ajuda.stderr)
    except FileNotFoundError:
        return False


def checar_rubberband():
    if shutil.which("rubberband") is None:
        sys.exit("[ERRO] 'rubberband' não encontrado. No Colab rode antes:\n"
                 "       !apt-get install -y rubberband-cli")


def esticar_e_transpor(y, sr, razao_tempo, semitons, preservar_formantes, usar_fine):
    """Uma chamada só do rubberband muda tempo e tom juntos.
    razao_tempo = duração_saída / duração_entrada (<1 = mais rápido)."""
    with tempfile.TemporaryDirectory() as tmp:
        entrada = os.path.join(tmp, "in.wav")
        saida = os.path.join(tmp, "out.wav")
        sf.write(entrada, y, sr, subtype="FLOAT")

        cmd = ["rubberband", "-q", "-t", f"{razao_tempo:.8f}"]
        if semitons != 0:
            cmd += ["-p", str(semitons)]
        if preservar_formantes:
            cmd.append("--formant")
        if usar_fine:
            cmd.append("--fine")
        cmd += [entrada, saida]

        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"rubberband falhou: {r.stderr.strip()[-300:]}")
        out, _ = sf.read(saida, dtype="float32", always_2d=True)
        return out


# ---------- áudio ----------

def carregar_stem(caminho):
    y, sr = sf.read(caminho, dtype="float32", always_2d=True)
    if y.shape[1] == 1:
        y = np.column_stack((y[:, 0], y[:, 0]))
    y = y[:, :2]
    if sr != SAMPLE_RATE:
        y = resample_poly(y, SAMPLE_RATE, sr, axis=0).astype(np.float32)
    return y


def detectar_inicio(stems_dict):
    """Primeiro beat forte da mistura dos stems (em segundos)."""
    n = min(len(v) for v in stems_dict.values())
    mono = sum(v[:n].mean(axis=1) for v in stems_dict.values())
    pico = np.max(np.abs(mono))
    if pico < 1e-6:
        return 0.0
    mono = mono / pico
    env = librosa.onset.onset_strength(y=mono, sr=SAMPLE_RATE)
    onsets = librosa.onset.onset_detect(onset_envelope=env, sr=SAMPLE_RATE,
                                        units="time", backtrack=False)
    if len(onsets) == 0:
        return 0.0
    # onset_detect marca o centro do frame: volta meio frame de análise
    inicio = max(0.0, float(onsets[0]) - 512 / SAMPLE_RATE)
    return 0.0 if inicio < 0.03 else inicio


def amostras_do_loop():
    seg = COMPASSOS_POR_LOOP * TEMPOS_POR_COMPASSO * 60.0 / BPM_ALVO
    return int(round(seg * SAMPLE_RATE))


def ajustar_tamanho(y, n):
    if len(y) >= n:
        return y[:n]
    return np.vstack([y, np.zeros((n - len(y), y.shape[1]), dtype=np.float32)])


def fade(y, ms=5):
    """Fade curtíssimo nas pontas: tira clique caso o corte caia no meio de onda."""
    k = min(int(SAMPLE_RATE * ms / 1000), len(y) // 2)
    if k > 0:
        rampa = np.linspace(0, 1, k, dtype=np.float32)[:, None]
        y[:k] *= rampa
        y[-k:] *= rampa[::-1]
    return y


def rms_db(y):
    r = np.sqrt(np.mean(np.square(y, dtype=np.float64)))
    return 20 * np.log10(max(r, 1e-9))


# ---------- processamento de uma música ----------

def processar_musica(nome, cfg, usar_fine):
    print(f"\n=== {nome} ===")
    pasta_in = os.path.join(PASTA_STEMS_ORIGINAIS, nome)

    stems = {}
    for s in STEMS:
        caminho = os.path.join(pasta_in, f"{s}.wav")
        if not os.path.exists(caminho):
            print(f"  [PULADA] não achei {caminho}")
            return None
        stems[s] = carregar_stem(caminho)

    bpm = float(cfg["bpm"])
    avisos = []

    # --- tom ---
    if cfg.get("semitons") is not None:
        semitons, tem_tom = int(cfg["semitons"]), True
    else:
        semitons, tem_tom = calcular_semitons(cfg.get("tom"), cfg.get("modo"))
    if not tem_tom:
        avisos.append("tom não informado: música NÃO foi transposta")
    elif abs(semitons) > 3:
        avisos.append(f"transposição de {semitons:+d} semitons (acima de 3 soa processado)")

    # --- tempo ---
    razao_tempo = bpm / BPM_ALVO
    mudanca = abs(BPM_ALVO / bpm - 1) * 100
    if mudanca > 5:
        avisos.append(f"mudança de tempo de {mudanca:.1f}% (acima de 5% soa processado)")

    # --- início do groove ---
    if cfg.get("inicio_s") is not None:
        inicio = float(cfg["inicio_s"])
        origem_inicio = "manual"
    else:
        inicio = detectar_inicio(stems)
        origem_inicio = "automático"

    compassos_clip = int(cfg.get("compassos_clip") or COMPASSOS_POR_LOOP)
    if compassos_clip > COMPASSOS_POR_LOOP or COMPASSOS_POR_LOOP % compassos_clip != 0:
        avisos.append(f"compassos_clip={compassos_clip} não divide {COMPASSOS_POR_LOOP}: "
                      f"vai sobrar silêncio no fim do loop")

    dur_clip = compassos_clip * TEMPOS_POR_COMPASSO * 60.0 / bpm
    ini_amostra = int(round(inicio * SAMPLE_RATE))
    n_clip = int(round(dur_clip * SAMPLE_RATE))
    util = min(len(v) for v in stems.values()) - ini_amostra
    if util < n_clip * 0.97:
        avisos.append(f"o clip tem {util / SAMPLE_RATE:.1f}s depois do início, mas "
                      f"{compassos_clip} compassos a {bpm:g} BPM pedem {dur_clip:.1f}s "
                      f"(o resto fica em silêncio)")
    # BPM implícito: confere se o BPM informado bate com a duração do clip
    if util > 0:
        bpm_implicito = compassos_clip * TEMPOS_POR_COMPASSO * 60.0 / (util / SAMPLE_RATE)
        if abs(bpm_implicito / bpm - 1) < 0.10 and abs(bpm_implicito / bpm - 1) > 0.015:
            avisos.append(f"pela duração do clip o BPM seria ~{bpm_implicito:.1f}, "
                          f"não {bpm:g} (confira o corte no Audacity)")

    # --- recorta, estica/transpõe, ajusta tamanho ---
    n_saida_clip = int(round(compassos_clip * TEMPOS_POR_COMPASSO * 60.0 / BPM_ALVO * SAMPLE_RATE))
    n_loop = amostras_do_loop()
    resultado = {}
    for s in STEMS:
        y = ajustar_tamanho(stems[s][ini_amostra:ini_amostra + n_clip], n_clip)
        y = esticar_e_transpor(y, SAMPLE_RATE, razao_tempo, semitons, preservar_formantes=(s == "vocals"),
                               usar_fine=usar_fine)
        y = ajustar_tamanho(y, n_saida_clip)
        repeticoes = max(1, n_loop // max(n_saida_clip, 1))
        y = np.tile(y, (repeticoes, 1)) if repeticoes > 1 else y
        resultado[s] = ajustar_tamanho(y, n_loop).astype(np.float32)

    # --- volume (mesmo ganho nos 3 stems) ---
    mistura = sum(resultado.values())
    ganho = 10 ** ((RMS_ALVO_DB - rms_db(mistura)) / 20)
    pico = float(np.max(np.abs(mistura))) or 1e-9
    if pico * ganho > PICO_MAX_MISTURA:
        ganho = PICO_MAX_MISTURA / pico
    for s in STEMS:
        resultado[s] = fade(resultado[s] * ganho)

    # --- salva ---
    pasta_out = os.path.join(PASTA_SAIDA, nome)
    os.makedirs(pasta_out, exist_ok=True)
    for s in STEMS:
        sf.write(os.path.join(pasta_out, f"{s}.wav"), resultado[s], SAMPLE_RATE,
                 subtype="PCM_16")

    print(f"  início do groove: {inicio:.2f}s ({origem_inicio})")
    print(f"  tempo: {bpm:g} -> {BPM_ALVO} BPM ({(BPM_ALVO / bpm - 1) * 100:+.1f}% de velocidade)")
    print(f"  tom: {semitons:+d} semitons" if tem_tom else "  tom: sem transposição")
    print(f"  ganho aplicado: {20 * np.log10(ganho):+.1f} dB")
    for a in avisos:
        print(f"  [ATENÇÃO] {a}")
    print(f"  OK -> {pasta_out}/ ({n_loop / SAMPLE_RATE:.2f}s por stem)")
    return resultado


def gerar_previews(biblioteca):
    """Combinações cruzadas pra ouvir se as músicas encaixam: bateria de uma,
    vocal da seguinte, outros da terceira."""
    nomes = list(biblioteca)
    if len(nomes) < 2:
        return
    pasta = os.path.join(PASTA_SAIDA, "_preview")
    os.makedirs(pasta, exist_ok=True)
    print("\n=== Previews de combinação ===")
    for i, base in enumerate(nomes):
        a, b, c = base, nomes[(i + 1) % len(nomes)], nomes[(i + 2) % len(nomes)]
        mix = biblioteca[a]["drums"] + biblioteca[b]["vocals"] + biblioteca[c]["other"]
        pico = np.max(np.abs(mix))
        if pico > 0.98:
            mix = mix / pico * 0.98
        # 2 voltas do loop pra dar tempo de perceber o encaixe
        mix = np.tile(mix, (2, 1))
        arq = os.path.join(pasta, f"{i + 1}_bat-{a}__voc-{b}__out-{c}.wav".replace(" ", "_"))
        sf.write(arq, mix, SAMPLE_RATE, subtype="PCM_16")
        print(f"  bateria {a} + vocal {b} + outros {c}")
    print(f"  salvos em {pasta}/")


def main():
    parser = argparse.ArgumentParser(description="Gera stems_prontos/ para o RockMachine")
    parser.add_argument("musicas", nargs="*", help="nomes das músicas (padrão: todas)")
    parser.add_argument("--preview", action="store_true",
                        help="gera combinações cruzadas pra ouvir o encaixe")
    args = parser.parse_args()

    checar_rubberband()
    usar_fine = _rubberband_tem_fine()
    escolhidas = args.musicas or list(MUSICAS)
    desconhecidas = [m for m in escolhidas if m not in MUSICAS]
    if desconhecidas:
        sys.exit(f"[ERRO] Não está no dicionário MUSICAS: {desconhecidas}")

    print(f"Alvo: {BPM_ALVO} BPM, {TOM_ALVO[0]} {TOM_ALVO[1]}, "
          f"{COMPASSOS_POR_LOOP} compassos = {amostras_do_loop() / SAMPLE_RATE:.2f}s por loop")

    biblioteca = {}
    for nome in escolhidas:
        try:
            r = processar_musica(nome, MUSICAS[nome], usar_fine)
        except Exception as e:
            print(f"  [ERRO] {nome}: {e}")
            continue
        if r is not None:
            biblioteca[nome] = r

    if args.preview:
        gerar_previews(biblioteca)

    print(f"\n=== Concluído: {len(biblioteca)}/{len(escolhidas)} música(s) em {PASTA_SAIDA}/ ===")


if __name__ == "__main__":
    main()