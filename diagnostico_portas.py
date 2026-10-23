import threading
import time

import serial

PORTAS = ["COM23", "COM5", "COM14"]   # confira se são os seus
BAUD = 9600


def ler(porta):
    try:
        s = serial.Serial(porta, BAUD, timeout=0.5)
    except Exception as e:
        print(f"[{porta}] NAO ABRIU: {e}")
        return
    print(f"[{porta}] aberta")
    while True:
        try:
            linha = s.readline().decode(errors="ignore").strip()
        except Exception as e:
            print(f"[{porta}] ERRO: {e}")
            return
        if linha:
            print(f"{time.strftime('%H:%M:%S')} [{porta}] {linha}")


for p in PORTAS:
    threading.Thread(target=ler, args=(p,), daemon=True).start()

print("Escutando. Passe um cartão em um leitor por vez. Ctrl+C para sair.")
while True:
    time.sleep(1)
