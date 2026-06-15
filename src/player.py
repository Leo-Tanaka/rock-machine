import pygame
import time
import os

class RockMachinePlayer:
    def __init__(self):
        # Inicializa o mixer do Pygame com configurações de baixa latência
        pygame.mixer.pre_init(44100, -16, 2, 512)
        pygame.init()
        self.canais = {
            "bateria": pygame.mixer.Channel(0),
            "baixo": pygame.mixer.Channel(1),
            "melodia": pygame.mixer.Channel(2),
            "voz": pygame.mixer.Channel(3)
        }
        self.sons_ativos = {}

    def carregar_faixa(self, instrumento, caminho_arquivo):
        if os.path.exists(caminho_arquivo):
            som = pygame.mixer.Sound(caminho_arquivo)
            self.sons_ativos[instrumento] = som
            print(f"[Player] {instrumento} carregado.")
        else:
            print(f"[Erro] Arquivo {caminho_arquivo} não encontrado.")

    def tocar_tudo_em_sync(self):
        print("\n🎶 Iniciando a Jam Session! Pressione Ctrl+C para parar.")
        # O pulo do gato: dar o play em todos os canais travados em um laço rápido
        for instrumento, canal in self.canais.items():
            if instrumento in self.sons_ativos:
                # O -1 faz o áudio rodar em loop infinito
                canal.play(self.sons_ativos[instrumento], loops=-1) 

        try:
            # Mantém o script rodando enquanto toca
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.parar_tudo()

    def parar_tudo(self):
        print("\nParando a música...")
        pygame.mixer.stop()

# --- TESTE DO PLAYER ---
if __name__ == "__main__":
    player = RockMachinePlayer()
    
    # Aqui você carrega os arquivos processados da pasta temp
    # Exemplo (substitua pelos arquivos que o dsp_engine gerar):
    player.carregar_faixa("bateria", "../temp/bateria_sync.wav")
    player.carregar_faixa("baixo", "../temp/baixo_sync.wav")
    player.carregar_faixa("melodia", "../temp/guitarra_sync.wav")

    player.tocar_tudo_em_sync()