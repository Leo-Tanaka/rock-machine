"""
RockMachine - Ponte serial (Arduino -> motor de áudio)
=========================================================
Lê os eventos ON:/OFF: dos 3 leitores fixos e aciona o motor de áudio.

Cada POSIÇÃO (leitor) tem um instrumento fixo — baixo é ignorado:
    0 = bateria (drums)
    1 = vocal   (vocals)
    2 = outros  (other)

Cada FICHA (UID) é ou:
    - uma MÚSICA normal (toca o stem do instrumento daquela posição,
      sincronizado em loop) -> cadastre em UID_PARA_MUSICA
    - um EFEITO imediato (toca uma vez, sem sincronizar, começa ao
      colocar e para ao retirar) -> cadastre em UID_PARA_EFEITO

Requisitos: pip install pyserial sounddevice soundfile numpy
"""

import queue
import sys
import threading
import time

import serial

from rockmachine_motor import RockMachinePlayer, BPM_ALVO, COMPASSOS_POR_LOOP, TEMPOS_POR_COMPASSO, PASTA_STEMS

# ============ CONFIGURAÇÕES ============

# Uma porta USB por Arduino (1 leitor cada). A ORDEM NÃO IMPORTA: a posição
# (0=bateria, 1=vocal, 2=outros) vem dentro da mensagem ON:/OFF: de cada
# placa, definida pela constante POSICAO no firmware. Ajuste os COM abaixo
# conforme o Gerenciador de Dispositivos do Windows mostrar.
PORTAS_SERIAIS = ["COM23", "COM14", "COM5"]
BAUD_RATE = 9600

# Compatibilidade com scripts antigos que importam PORTA_SERIAL
PORTA_SERIAL = PORTAS_SERIAIS[0]

# Instrumento fixo de cada posição (índice do leitor no Arduino).
# Baixo não entra — só bateria, vocal e outros.
INSTRUMENTO_POR_POSICAO = {
    0: "drums",
    1: "vocals",
    2: "other",
}

# UID de ficha "normal" -> nome da música (a pasta dentro de stems_prontos/)
UID_PARA_MUSICA = {
    "73660EF7": "Billie Jean",
    "2A634C73": "Numb",
    "1AD3781A": "Paparazzi",
    "CA4B6C06": "Tudo Que Ela Gosta De Escutar",
    "AEF76C06": "Get Lucky",
    # adicione mais linhas aqui conforme cadastrarem novas fichas
}

# UID de ficha "efeito" -> (pasta da música, nome do arquivo de efeito)
# O arquivo precisa existir em stems_prontos/<pasta>/<arquivo>.wav
UID_PARA_EFEITO = {
    "4BA49079": ("Another One Bites the Dust", "gemido-whatsapp"),
}

# ======================================================


# posicao_id -> "normal" ou "efeito", pra saber qual método chamar no OFF
_tipo_ativo_por_posicao = {}


def processar_linha(linha, player):
    linha = linha.strip()
    if not linha:
        return

    if linha == "READY":
        print("[Arduino] Conectado e pronto.")
        return

    if linha.startswith("ON:"):
        partes = linha.split(":")
        if len(partes) != 3:
            print(f"[AVISO] Linha ON malformada: {linha}")
            return
        _, posicao_str, uid = partes
        posicao = int(posicao_str)

        if uid in UID_PARA_EFEITO:
            musica, arquivo = UID_PARA_EFEITO[uid]
            player.iniciar_efeito(posicao, musica, arquivo)
            _tipo_ativo_por_posicao[posicao] = "efeito"
            return

        if uid in UID_PARA_MUSICA:
            instrumento = INSTRUMENTO_POR_POSICAO.get(posicao)
            if instrumento is None:
                print(f"[AVISO] Posição {posicao} sem instrumento mapeado.")
                return
            musica = UID_PARA_MUSICA[uid]
            player.colocar_ficha(posicao, musica, instrumento)
            _tipo_ativo_por_posicao[posicao] = "normal"
            return

        print(f"[AVISO] UID desconhecido na posição {posicao}: {uid} — cadastre em UID_PARA_MUSICA ou UID_PARA_EFEITO.")
    elif linha.startswith("OFF:"):
        partes = linha.split(":")
        if len(partes) != 2:
            print(f"[AVISO] Linha OFF malformada: {linha}")
            return
        posicao = int(partes[1])

        tipo = _tipo_ativo_por_posicao.pop(posicao, None)
        if tipo == "efeito":
            player.parar_efeito(posicao)
        else:
            player.retirar_ficha(posicao)

    else:
        print(f"[Arduino] {linha}")


class LeitoresSerial:
    """Abre várias portas seriais (1 Arduino por porta) e junta todas as
    linhas numa fila única. Cada porta tem sua própria thread de leitura;
    o áudio só é tocado pela thread principal (processar_pendentes), então
    não há corrida entre leitores.

    - Se uma porta não abrir, o programa segue com as outras (o teclado
      cobre a posição que ficou de fora).
    - Se um Arduino for desconectado, manda OFF das posições dele (pra
      não ficar som preso) e tenta reconectar sozinho a cada 2 s.
    """

    def __init__(self, portas, baud=BAUD_RATE):
        self.portas = list(portas)
        self.baud = baud
        self.fila = queue.Queue()
        self._parar = threading.Event()
        self._threads = []
        self._seriais = {}
        self.conectadas = set()

    def iniciar(self):
        for porta in self.portas:
            t = threading.Thread(target=self._loop_porta, args=(porta,), daemon=True)
            t.start()
            self._threads.append(t)

    def _abrir(self, porta):
        try:
            s = serial.Serial(porta, self.baud, timeout=0.1)
        except (serial.SerialException, OSError) as e:
            return None, e
        time.sleep(2)  # Arduino reinicia ao abrir a porta
        s.reset_input_buffer()
        return s, None

    def _loop_porta(self, porta):
        avisou_falha = False
        while not self._parar.is_set():
            s, erro = self._abrir(porta)
            if s is None:
                if not avisou_falha:
                    print(f"[AVISO] {porta} não abriu ({erro}). Vou continuar tentando; "
                          f"enquanto isso use o teclado nessa posição.")
                    avisou_falha = True
                self._parar.wait(2)
                continue

            avisou_falha = False
            self._seriais[porta] = s
            self.conectadas.add(porta)
            print(f"[OK] {porta} conectada.")
            ativas = set()  # posições com ficha nesta placa

            try:
                while not self._parar.is_set():
                    linha = s.readline().decode(errors="ignore").strip()
                    if not linha:
                        continue
                    try:
                        if linha.startswith("ON:"):
                            ativas.add(int(linha.split(":")[1]))
                        elif linha.startswith("OFF:"):
                            ativas.discard(int(linha.split(":")[1]))
                    except (ValueError, IndexError):
                        pass  # linha corrompida: processar_linha reclama depois
                    self.fila.put(linha)
            except (serial.SerialException, OSError) as e:
                if not self._parar.is_set():
                    print(f"[AVISO] {porta} desconectou ({e}). Tentando reconectar...")
            finally:
                self.conectadas.discard(porta)
                self._seriais.pop(porta, None)
                try:
                    s.close()
                except Exception:
                    pass
                for pos in ativas:  # solta o som das posições dessa placa
                    self.fila.put(f"OFF:{pos}")

    def processar_pendentes(self, player, timeout=0.05):
        """Processa tudo que chegou. Espera até `timeout` s se não há nada
        (serve de 'respiro' pro loop principal)."""
        try:
            linha = self.fila.get(timeout=timeout)
        except queue.Empty:
            return
        while True:
            try:
                processar_linha(linha, player)
            except Exception as e:  # uma linha ruim não pode derrubar o show
                print(f"[ERRO] Falha processando '{linha}': {e}")
            try:
                linha = self.fila.get_nowait()
            except queue.Empty:
                break

    def fechar(self):
        self._parar.set()
        for s in list(self._seriais.values()):
            try:
                s.close()
            except Exception:
                pass


def main():
    print(f"Conectando em {len(PORTAS_SERIAIS)} porta(s): {', '.join(PORTAS_SERIAIS)} ({BAUD_RATE} baud)...")
    leitores = LeitoresSerial(PORTAS_SERIAIS, BAUD_RATE)
    leitores.iniciar()

    print("Iniciando motor de áudio (stems carregam sob demanda)...")
    player = RockMachinePlayer(PASTA_STEMS, BPM_ALVO, COMPASSOS_POR_LOOP, TEMPOS_POR_COMPASSO)

    with player.iniciar_stream():
        print("\n=== RockMachine rodando — escutando os Arduinos ===")
        print("Posições: 0=bateria, 1=vocal, 2=outros (baixo ignorado)")
        print("Ctrl+C para sair.\n")

        try:
            while True:
                leitores.processar_pendentes(player)
        except KeyboardInterrupt:
            print("\nEncerrando...")
        finally:
            leitores.fechar()


if __name__ == "__main__":
    main()