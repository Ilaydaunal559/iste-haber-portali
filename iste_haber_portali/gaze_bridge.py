"""
Gazepoint köprüsü.

NEDEN GEREKLİ?
    Gazepoint Control, 127.0.0.1:4242 portunda bir TCP sunucusu açar ve bakış verisini
    XML satırları olarak yollar. Tarayıcı JavaScript'i ham TCP soketine bağlanamaz.
    Bu yüzden Python araya girer:

        Gazepoint (TCP:4242)  ->  bu dosya (thread)  ->  Flask SSE (/gaze/stream)  ->  tarayıcı

GELEN VERİ ÖRNEĞİ (satır sonu \\r\\n):
    <REC TIME="12.345" BPOGX="0.52" BPOGY="0.31" BPOGV="1" />
    BPOGX/BPOGY : bakış noktası, ekranın 0-1 arası oransal koordinatı (sol-üst = 0,0)
    BPOGV       : 1 ise ölçüm geçerli (göz bulundu), 0 ise geçersiz
"""
import json
import queue
import re
import socket
import threading
import time

HOST = "127.0.0.1"
PORT = 4242

KOMUTLAR = [
    '<SET ID="ENABLE_SEND_POG_BEST" STATE="1" />',   # "en iyi" bakış noktası (iki gözün birleşimi)
    '<SET ID="ENABLE_SEND_DATA" STATE="1" />',       # veri akışını başlat
]

RE_X = re.compile(r'BPOGX="([-\d.eE+]+)"')
RE_Y = re.compile(r'BPOGY="([-\d.eE+]+)"')
RE_V = re.compile(r'BPOGV="(\d)"')


class GazeKoprusu(threading.Thread):
    """Arka planda çalışır, bağlantı koparsa kendini yeniden bağlar."""

    def __init__(self, host=HOST, port=PORT):
        super().__init__(daemon=True)           # ana program kapanınca bu da kapansın
        self.host, self.port = host, port
        self.durum = "bagli_degil"              # bagli_degil | bagli_veri_yok | veri_akiyor
        self._aboneler = set()
        self._kilit = threading.Lock()

    # --- tarayıcı bağlantıları (her açık sayfa bir "abone") -----------------
    def abone_ol(self):
        kuyruk = queue.Queue(maxsize=200)
        with self._kilit:
            self._aboneler.add(kuyruk)
        return kuyruk

    def abonelik_iptal(self, kuyruk):
        with self._kilit:
            self._aboneler.discard(kuyruk)

    def _yayinla(self, nokta):
        with self._kilit:
            aboneler = list(self._aboneler)
        for k in aboneler:
            try:
                k.put_nowait(nokta)
            except queue.Full:                  # yavaş istemci: eski veriyi atla, bekleme
                pass

    # --- ana döngü ------------------------------------------------------------
    def run(self):
        while True:
            try:
                self._baglan_ve_oku()
            except OSError:
                pass                            # Gazepoint kapalı / bağlantı koptu
            self.durum = "bagli_degil"
            time.sleep(3)                       # 3 sn sonra tekrar dene

    def _baglan_ve_oku(self):
        with socket.create_connection((self.host, self.port), timeout=5) as s:
            s.settimeout(2)
            for komut in KOMUTLAR:
                s.sendall((komut + "\r\n").encode())
            self.durum = "bagli_veri_yok"

            tampon = ""
            son_gecerli = 0.0
            while True:
                try:
                    parca = s.recv(4096)
                except socket.timeout:
                    if time.time() - son_gecerli > 3:
                        self.durum = "bagli_veri_yok"
                    continue
                if not parca:
                    raise ConnectionError("Gazepoint bağlantıyı kapattı")

                # Mesajlar \r\n ile biter; yarım gelen son parçayı bir sonraki tura sakla
                tampon += parca.decode("utf-8", errors="ignore")
                *satirlar, tampon = tampon.split("\r\n")

                for satir in satirlar:
                    nokta = self._ayikla(satir)
                    if nokta:
                        son_gecerli = time.time()
                        self.durum = "veri_akiyor"
                        self._yayinla(nokta)

    @staticmethod
    def _ayikla(satir):
        if not satir.startswith("<REC"):
            return None                         # <ACK ...> gibi onay mesajlarını atla
        v, x, y = RE_V.search(satir), RE_X.search(satir), RE_Y.search(satir)
        if not (v and x and y) or v.group(1) != "1":
            return None                         # geçersiz ölçüm (göz kırpma, göz kaybı)
        return {"x": float(x.group(1)), "y": float(y.group(1))}


kopru = GazeKoprusu()


if __name__ == "__main__":
    # Tek başına test: Gazepoint açıkken terminalde bakış koordinatlarını gösterir
    kopru.start()
    k = kopru.abone_ol()
    print("Dinleniyor... (Ctrl+C ile çık)")
    while True:
        try:
            print(json.dumps(k.get(timeout=2)), "|", kopru.durum)
        except queue.Empty:
            print("veri yok |", kopru.durum)
