import qrcode

url = "https://rock-machine.onrender.com/playlist"

qr = qrcode.make(url)
qr.save("qrcode_playlist.png")

print("QR Code criado!")