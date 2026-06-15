import librosa
import soundfile as sf
import os
import warnings

# Ignorar os avisos chatos do PySoundFile no terminal
warnings.filterwarnings('ignore', category=UserWarning)

class MotorDSP:
    def __init__(self, bpm_global, tom_global_id):
        self.bpm_global = bpm_global
        self.tom_global_id = tom_global_id # Ex: 0 = C, 1 = C#, 2 = D... (Simplificado para o MVP)
        
    def processar_faixa(self, caminho_entrada, caminho_saida, bpm_original, tom_original_id):
        print(f"Processando: {caminho_entrada}...")
        
        # 1. Carregar o áudio
        # sr=None mantém a taxa de amostragem original
        y, sr = librosa.load(caminho_entrada, sr=None)
        
        # 2. Sincronia de Tempo (Time-Stretch)
        # Calcula a razão entre o BPM desejado e o original
        rate_tempo = self.bpm_global / bpm_original
        if rate_tempo != 1.0:
            print(f" -> Ajustando tempo: {bpm_original} BPM para {self.bpm_global} BPM")
            y = librosa.effects.time_stretch(y, rate=rate_tempo)
            
        # 3. Sincronia de Tom (Pitch-Shift)
        # Calcula a diferença em semitons
        passos_pitch = self.tom_global_id - tom_original_id
        if passos_pitch != 0:
            print(f" -> Ajustando tom: {passos_pitch} semitons")
            y = librosa.effects.pitch_shift(y, sr=sr, n_steps=passos_pitch)
            
        # 4. Salvar o arquivo processado na pasta temp
        sf.write(caminho_saida, y, sr)
        print(f"Concluído: {caminho_saida}\n")
        return caminho_saida

# --- TESTE DO MOTOR DSP ---
if __name__ == "__main__":
    # Configuração Global da nossa "Sessão Jam"
    motor = MotorDSP(bpm_global=120, tom_global_id=1) # Alvo: 120 BPM, Tom C (Dó)
    
    # Simulação: Você tem um baixo de uma música a 100 BPM em Ré (D = ID 2)
    # Certifique-se de ter um arquivo 'baixo_teste.wav' na pasta assets!
    caminho_in = "../assets/getlucky_drums.wav"
    caminho_out = "../temp/bateria_sync.wav"
    
    if os.path.exists(caminho_in):
        motor.processar_faixa(
            caminho_entrada=caminho_in,
            caminho_saida=caminho_out,
            bpm_original=116, 
            tom_original_id=0 
        )
    else:
        print(f"Erro: Coloque um arquivo de teste em {caminho_in} para testar!")