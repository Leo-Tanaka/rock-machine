"""
RockMachine - Teste isolado de publicação (sem precisar do Arduino)
======================================================================
Gera um WAV de teste bem curto e tenta publicar na playlist de verdade,
só pra confirmar que a conexão com o backend está funcionando — sem
precisar ligar nada de hardware.

Rode: python teste_publicar.py
"""

import numpy as np
import soundfile as sf

from gravar_sessao import publicar_musica

CAMINHO_TESTE = "teste_publicacao.wav"


def main():
    print("Gerando um WAV de teste (3 segundos de silêncio com um beep)...")
    sr = 44100
    duracao = 3.0
    t = np.linspace(0, duracao, int(sr * duracao), endpoint=False)
    # Um beep de 1s só pra não ser um arquivo totalmente vazio/silencioso
    beep = (np.sin(2 * np.pi * 440 * t) * 0.2 * (t < 1.0)).astype("float32")
    sf.write(CAMINHO_TESTE, beep, sr)

    print("Tentando publicar na playlist de verdade...")
    sucesso = publicar_musica(
        CAMINHO_TESTE,
        nome="TESTE - pode ignorar/remover",
        autor="Teste automático",
    )

    print()
    if sucesso:
        print("Deu certo! Confira em https://rock-machine.onrender.com/playlist")
        print("se essa música de teste apareceu na lista.")
    else:
        print("Não conseguiu publicar — revise a mensagem de erro acima.")


if __name__ == "__main__":
    main()
