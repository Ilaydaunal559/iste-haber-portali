"""
İSTE haber çekici (https://iste.edu.tr)

Kullanım:
    python iste_news.py                      -> yeni haber/duyuruları çek, okul.db'ye yaz
    python iste_news.py --kesif              -> bulunan linkleri göster (kaydetmez)
    python iste_news.py --listele            -> veritabanındaki son kayıtları göster
    python iste_news.py --tarih 3 2026-11-15 -> 3 numaralı kayda elle son tarih ver

SİTENİN YAPISI (iste.edu.tr'yi inceleyerek):
    Haber         https://iste.edu.tr/haber-merkezi/2026/10/28/3303
    Duyuru        https://iste.edu.tr/duyuru-merkezi/2026/10/22/7141
                  https://iste.edu.tr/duyuru-merkezi/oidb/2026/09/28/7049   (arada birim adı olabilir)
    Akademik      https://iste.edu.tr/akademik-calismalar/2026/10/21/3291
    -> Hepsi  /<bölüm>/[birim/]YYYY/AA/GG/<numara>  kalıbında. Yayın tarihi adresin içinde!

MANTIK:
    1) Ana sayfa + 3 liste sayfasını indir
    2) Kalıba uyan linkleri bul (link metni = başlık, başındaki "28 Eki" kısmı atılır)
    3) Veritabanında OLMAYAN her kaydın sayfasına gir, metni al
    4) Metinde "son başvuru ... 15.11.2026" gibi kalıp ara -> geri sayımın hedefi
    5) INSERT OR IGNORE ile kaydet (url UNIQUE: aynı haber iki kez eklenmez)
"""
import argparse
import re
import sys
import time
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from db import db_baglan, tablo_olustur

# ---------------------------------------------------------------------------
# AYARLAR
# ---------------------------------------------------------------------------
KAYNAK_SAYFALAR = [
    "https://iste.edu.tr/",
    "https://iste.edu.tr/haber-merkezi",
    "https://iste.edu.tr/duyuru-merkezi",
    "https://iste.edu.tr/akademik-calismalar",
]
LINK_KALIBI = re.compile(
    r"^https?://(?:www\.)?iste\.edu\.tr/(haber-merkezi|duyuru-merkezi|akademik-calismalar)"
    r"/(?:[a-z0-9-]+/)?(\d{4})/(\d{2})/(\d{2})/(\d+)/?$"
)
KATEGORI = {"haber-merkezi": "haber", "duyuru-merkezi": "duyuru", "akademik-calismalar": "akademik"}
MAX_YENI = 40                 # bir çalıştırmada en fazla kaç yeni kayıt işlensin
BEKLEME_SN = 0.8              # istekler arası bekleme: siteyi yormamak için
HEADERS = {"User-Agent": "Mozilla/5.0 (okul projesi; norobilisim dersi)"}

# ---------------------------------------------------------------------------
# TARİH TESPİTİ
# ---------------------------------------------------------------------------
AYLAR = {
    "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6,
    "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12,
}
TR_KUCUK = str.maketrans({"İ": "i", "I": "ı"})   # "İ".lower() 2 karakter üretir; önce elle çevir

SAYISAL_TARIH = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")
YAZILI_TARIH = re.compile(r"\b(\d{1,2})\s+(" + "|".join(AYLAR) + r")\s+(\d{4})\b")
# Tek bir "bitiş" bildiren ifadeler: en yakın tarihi al
BITIS_ANAHTAR = re.compile(r"son\s+başvuru|son\s+tarih|son\s+gün|son\s+teklif|tarihine\s+kadar|kadar\s+başvur")
# "10.11.2026 - 20.11.2026 tarihleri arasında" gibi aralıklar: ikisinden SONRAKİ bitiştir
ARALIK_ANAHTAR = re.compile(r"tarihleri\s+arasında|tarihleri\s+arası")


def tr_kucult(metin):
    return metin.translate(TR_KUCUK).lower()


def son_tarih_bul(metin, bugun=None):
    """
    Metinde başvuru bitişini bildiren ifadenin yakınındaki GELECEK tarihi arar.
    Bulursa 'YYYY-MM-DDT23:59:00' döner, yoksa None.
    Birden çok aday varsa bugüne en yakın (en acil) olanı seçer.
    """
    bugun = bugun or date.today()
    kucuk = tr_kucult(metin)

    tarihler = []                                  # (konum, date)
    for m in SAYISAL_TARIH.finditer(kucuk):
        g, a, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        tarihler.append((m.start(), g, a, y))
    for m in YAZILI_TARIH.finditer(kucuk):
        tarihler.append((m.start(), int(m.group(1)), AYLAR[m.group(2)], int(m.group(3))))

    gecerli = []
    for konum, g, a, y in tarihler:
        try:
            gecerli.append((konum, date(y, a, g)))
        except ValueError:                         # 31.02.2026 gibi geçersiz tarih
            pass

    adaylar = []
    for k in BITIS_ANAHTAR.finditer(kucuk):
        yakin = [(abs(konum - k.start()), d) for konum, d in gecerli if abs(konum - k.start()) <= 150]
        if yakin:
            adaylar.append(min(yakin)[1])          # anahtar kelimeye en yakın tarih
    for k in ARALIK_ANAHTAR.finditer(kucuk):
        yakin = [d for konum, d in gecerli if abs(konum - k.start()) <= 80]
        if yakin:
            adaylar.append(max(yakin))             # aralığın sonu

    gelecek = [d for d in adaylar if d >= bugun]
    if not gelecek:
        return None
    return f"{min(gelecek).isoformat()}T23:59:00"


# ---------------------------------------------------------------------------
# SİTEDEN VERİ ÇEKME
# ---------------------------------------------------------------------------
def sayfa_al(url):
    cevap = requests.get(url, headers=HEADERS, timeout=20)
    cevap.raise_for_status()                       # 404/500'de exception fırlat
    cevap.encoding = "utf-8"                       # site UTF-8; Türkçe karakterler bozulmasın
    return BeautifulSoup(cevap.text, "html.parser")


def url_normallestir(url):
    """www'lu/www'suz ve http/https varyantlarını tek forma indir (tekrar kaydı önler)."""
    url = url.split("#")[0].split("?")[0].rstrip("/")
    return re.sub(r"^https?://(?:www\.)?iste\.edu\.tr", "https://iste.edu.tr", url)


AY_ON_EK = re.compile(r"^\d{1,2}\s+(?:Oca|Şub|Mar|Nis|May|Haz|Tem|Ağu|Eyl|Eki|Kas|Ara)\s+")


def baslik_temizle(metin):
    """'28 Eki İSKENDERUN TEKNİK ...' -> 'İSKENDERUN TEKNİK ...'"""
    return AY_ON_EK.sub("", " ".join(metin.split())).strip()


def linkleri_bul():
    """Tüm kaynak sayfalardaki haber/duyuru linklerini topla -> {url: bilgi}"""
    bulunan = {}
    for kaynak in KAYNAK_SAYFALAR:
        try:
            soup = sayfa_al(kaynak)
        except requests.RequestException as hata:
            print("Kaynak sayfa açılamadı:", kaynak, "->", hata)
            continue
        for a in soup.find_all("a", href=True):
            tam = urljoin(kaynak, a["href"])
            m = LINK_KALIBI.match(tam)
            if not m:
                continue
            url = url_normallestir(tam)
            baslik = baslik_temizle(a.get_text(" ", strip=True))
            if len(baslik) < 8:                    # görsel linkleri ("#" vb.) başlık taşımaz
                baslik = ""
            eski = bulunan.get(url)
            if eski is None or (not eski["baslik"] and baslik):
                bulunan[url] = {
                    "baslik": baslik,
                    "kategori": KATEGORI[m.group(1)],
                    "tarih": f"{m.group(2)}-{m.group(3)}-{m.group(4)}",   # adresten: güvenilir
                }
        time.sleep(BEKLEME_SN)
    return bulunan


TARIH_SATIRI = re.compile(r"^\d{2}/\d{2}/\d{4}$")          # sayfadaki "28/10/2026" satırı
ALT_BILGI_ISARETLERI = ("İSKENDERUN TEKNİK ÜNİVERSİTESİ", "TAKİPTE KAL", "KURUMSAL")


def haber_detayi_al(url):
    """Haber sayfasından (baslik_yedek, icerik) döner."""
    soup = sayfa_al(url)

    # Yedek başlık: sayfada "28/10/2026" satırından hemen sonra gelen satır başlıktır
    satirlar = [s.strip() for s in soup.get_text("\n").splitlines() if s.strip()]
    yedek_baslik, tarih_idx = None, None
    for i, s in enumerate(satirlar):
        if TARIH_SATIRI.match(s) and i + 1 < len(satirlar):
            yedek_baslik, tarih_idx = satirlar[i + 1], i
            break

    # 1. yöntem: menü/alt bilgiyi at, <p> etiketlerini topla
    for etiket in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        etiket.decompose()
    paragraflar = []
    for p in soup.find_all("p"):
        metin = " ".join(p.get_text(" ", strip=True).split())
        if len(metin) < 40 or "temel hedefimiz" in metin:    # kısa çöp + alt bilgi tanıtım yazısı
            continue
        paragraflar.append(metin)

    # 2. yöntem (yedek): <p> yoksa, tarih satırından alt bilgiye kadar olan satırları al
    if not paragraflar and tarih_idx is not None:
        for s in satirlar[tarih_idx + 2:]:
            if s in ALT_BILGI_ISARETLERI:
                break
            if len(s) >= 40:
                paragraflar.append(s)

    # Özet tekrarını ele: site kısa özeti, tam metnin ilk cümlesi olarak da veriyor
    temiz = []
    for i, p in enumerate(paragraflar):
        sonraki = paragraflar[i + 1] if i + 1 < len(paragraflar) else ""
        if sonraki.startswith(p) or p in temiz:
            continue
        temiz.append(p)
    return yedek_baslik, "\n".join(temiz)


# ---------------------------------------------------------------------------
# KOMUTLAR
# ---------------------------------------------------------------------------
def kesif_modu():
    for url, b in sorted(linkleri_bul().items(), key=lambda x: x[1]["tarih"], reverse=True):
        print(f"{b['tarih']}  {b['kategori']:<9} {(b['baslik'] or '(başlıksız)')[:60]:<60} {url}")


def haberleri_cek():
    tablo_olustur()
    with db_baglan() as db:
        kayitli = {r["url"] for r in db.execute("SELECT url FROM haberler")}

    adaylar = linkleri_bul()
    yeniler = [(u, b) for u, b in adaylar.items() if u not in kayitli]
    yeniler.sort(key=lambda x: x[1]["tarih"], reverse=True)       # en yeniler önce
    print(f"{len(adaylar)} link bulundu, {len(yeniler)} tanesi yeni.\n")

    eklenen = 0
    with db_baglan() as db:
        for url, b in yeniler[:MAX_YENI]:
            try:
                yedek_baslik, icerik = haber_detayi_al(url)
            except requests.RequestException as hata:
                print("Atlandı:", url, "->", hata)
                continue

            baslik = b["baslik"] or yedek_baslik
            if not baslik:
                print("Başlık bulunamadı, atlandı:", url)
                continue

            son_tarih = son_tarih_bul(f"{baslik}\n{icerik}")
            db.execute(
                """INSERT OR IGNORE INTO haberler
                   (baslik, icerik, url, tarih, son_tarih, tarih_kaynak, kategori)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (baslik, icerik, url, b["tarih"], son_tarih,
                 "otomatik" if son_tarih else None, b["kategori"]),
            )
            eklenen += 1
            isaret = f"son: {son_tarih[:10]}" if son_tarih else "-"
            print(f"{b['tarih']} {b['kategori']:<9} {isaret:<16} {baslik[:60]}")
            time.sleep(BEKLEME_SN)
    print(f"\n{eklenen} kayıt eklendi.")


def listele():
    with db_baglan() as db:
        for r in db.execute("SELECT id, tarih, kategori, son_tarih, baslik FROM haberler "
                            "ORDER BY tarih DESC, id DESC LIMIT 25"):
            son = (r["son_tarih"] or "")[:10]
            print(f"{r['id']:>3}  {r['tarih']}  {(r['kategori'] or '-'):<9} {son:<10}  {r['baslik'][:60]}")


def tarihi_elle_ayarla(kayit_id, gun):
    datetime.strptime(gun, "%Y-%m-%d")             # biçim yanlışsa hata verir
    with db_baglan() as db:
        db.execute("UPDATE haberler SET son_tarih = ?, tarih_kaynak = 'elle' WHERE id = ?",
                   (f"{gun}T23:59:00", kayit_id))
    print(f"Kayıt {kayit_id} için son tarih: {gun}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="İSTE haber çekici")
    ap.add_argument("--kesif", action="store_true", help="linkleri göster, kaydetme")
    ap.add_argument("--listele", action="store_true", help="veritabanındaki kayıtları göster")
    ap.add_argument("--tarih", nargs=2, metavar=("ID", "YYYY-AA-GG"), help="elle son tarih ver")
    args = ap.parse_args()
    try:
        if args.kesif:
            kesif_modu()
        elif args.listele:
            listele()
        elif args.tarih:
            tarihi_elle_ayarla(int(args.tarih[0]), args.tarih[1])
        else:
            haberleri_cek()
    except requests.RequestException as hata:
        sys.exit(f"Siteye ulaşılamadı: {hata}")
