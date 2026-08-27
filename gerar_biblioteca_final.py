"""
RockMachine - Geração da biblioteca final de stems (linha de comando)
=======================================================================
Usa o MESMO motor de DSP do software "Máquina de Rock" (já corrigido:
sem o bug do librosa.beat.tempo, e com corte automático de silêncio/intro).

Diferença em relação ao app web: aqui não tem interface, é só rodar uma vez
pra gerar a biblioteca definitiva de stems prontos, que o player do hardware
vai usar direto (sem reprocessar nada em tempo real).

Como usar:
1. Coloque este arquivo dentro da pasta "rock-machine-main" (mesmo nível
   de server.py), porque ele importa o motor de DSP de dentro de src/.
2. Coloque os stems brutos gerados pelo Demucs em uma pasta local, seguindo
   a estrutura: stems_brutos/<nome da música>/<stem>.wav
3. Ajuste as configurações abaixo se necessário
4. Rode: python gerar_biblioteca_final.py
5. Os arquivos prontos aparecem em stems_prontos/<música>/<stem>.wav

Requisitos: os mesmos do requirements.txt do projeto (librosa, soundfile, numpy)
"""

import os
import sys
from pathlib import Path

import librosa

# Garante que "src" seja importável mesmo rodando este arquivo diretamente
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.dsp_engine import MotorDSP  # noqa: E402

# ============ CONFIGURAÇÕES — edite aqui ============

# BPM alvo comum do repertório
BPM_ALVO = 106

# Se True, força todos os stems para o MESMO tom (TOM_ALVO_ID abaixo).
# Se False, cada stem mantém seu próprio tom original (sem pitch-shift) —
# mais simples e é o que vocês vinham usando até agora.
TRANSPOR_TOM = False
TOM_ALVO_ID = 4  # 4 = E (só usado se TRANSPOR_TOM = True)

# Pasta com os stems brutos (baixados do Drive/Colab, ainda não processados)
PASTA_STEMS_BRUTOS = "stems_brutos"

# Pasta de saída com os stems prontos para o hardware tocar
PASTA_SAIDA = "stems_prontos"

# Músicas e stems a processar
MUSICAS = {
    "Another One Bites the Dust": ["drums", "bass", "vocals", "other"],
    "Espresso": ["drums", "bass", "vocals", "other"],
    "Levitating": ["drums", "bass", "vocals", "other"],
}

# ======================================================


def main():
    total_ok = 0
    total_erro = 0

    for nome_musica, stems in MUSICAS.items():
        print(f"\n=== {nome_musica} ===")
        pasta_entrada = Path(PASTA_STEMS_BRUTOS) / nome_musica
        pasta_saida = Path(PASTA_SAIDA) / nome_musica
        pasta_saida.mkdir(parents=True, exist_ok=True)

        for stem_nome in stems:
            caminho_entrada = pasta_entrada / f"{stem_nome}.wav"
            caminho_saida = pasta_saida / f"{stem_nome}.wav"

            if not caminho_entrada.exists():
                print(f"  [PULADO] Não encontrado: {caminho_entrada}")
                total_erro += 1
                continue

            try:
                print(f"  Analisando {stem_nome}...")
                # Detecção usa uma versão mono só para BPM/tom (mais estável)
                y_mono, sr = librosa.load(str(caminho_entrada), sr=None, mono=True)
                bpm_original = MotorDSP.detectar_bpm(y_mono, sr)
                tom_original_id = MotorDSP.detectar_tom_fundamental(y_mono, sr)

                tom_alvo = TOM_ALVO_ID if TRANSPOR_TOM else tom_original_id

                print(f"    Detectado: {bpm_original:.1f} BPM, tom id {tom_original_id}")

                motor = MotorDSP(bpm_global=BPM_ALVO, tom_global_id=tom_alvo)
                motor.processar_faixa(
                    caminho_entrada=caminho_entrada,
                    caminho_saida=caminho_saida,
                    bpm_original=bpm_original,
                    tom_original_id=tom_original_id,
                )
                total_ok += 1
            except Exception as e:
                print(f"  [ERRO] {stem_nome}: {e}")
                total_erro += 1

    print(f"\n=== Concluído ===")
    print(f"Stems gerados com sucesso: {total_ok}")
    print(f"Erros/pulados: {total_erro}")
    print(f"Biblioteca final em: {PASTA_SAIDA}/")
    print("\nEsses arquivos já estão sem silêncio no início, no mesmo BPM,")
    print("prontos para o player do hardware tocar diretamente ao detectar a ficha.")


if __name__ == "__main__":
    main()
