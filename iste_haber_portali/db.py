"""Veritabanı yardımcıları: tek bir yerde bağlantı ve tablo tanımı."""
import sqlite3
from contextlib import contextmanager

DB_YOLU = "okul.db"


@contextmanager
def db_baglan():
    """with db_baglan() as db: ... bloğu bitince otomatik commit + close yapar."""
    baglanti = sqlite3.connect(DB_YOLU)
    baglanti.row_factory = sqlite3.Row      # sütunlara isimle erişmek için: satir["baslik"]
    try:
        yield baglanti
        baglanti.commit()
    finally:
        baglanti.close()


def _sutun_ekle(db, tablo, sutun, tip):
    """Eski okul.db'de sütun yoksa ekler (veritabanını silmeden güncelleme)."""
    mevcut = {satir["name"] for satir in db.execute(f"PRAGMA table_info({tablo})")}
    if sutun not in mevcut:
        db.execute(f"ALTER TABLE {tablo} ADD COLUMN {sutun} {tip}")


def tablo_olustur():
    with db_baglan() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS haberler (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            baslik       TEXT NOT NULL,
            icerik       TEXT,
            url          TEXT UNIQUE,          -- aynı haber iki kez eklenmesin
            tarih        TEXT,                 -- yayın tarihi  (YYYY-MM-DD)
            son_tarih    TEXT,                 -- başvuru bitişi (YYYY-MM-DDTHH:MM:SS) veya NULL
            tarih_kaynak TEXT,                 -- 'otomatik' | 'elle' | 'demo'
            kategori     TEXT                  -- 'haber' | 'duyuru' | 'akademik'
        );

        -- Bakış / tıklama olayları: nörobilişim analizinin ham verisi
        CREATE TABLE IF NOT EXISTS olaylar (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            oturum   TEXT,                     -- tarayıcı oturumu (anonim)
            varyant  TEXT,                     -- 'kitlik' (geri sayım var) | 'kontrol' (yok)
            haber_id INTEGER,
            aoi      TEXT,                     -- bakılan bölge: baslik, icerik, geri-sayim, ...
            tur      TEXT,                     -- 'bakis' | 'tiklama'
            sure_ms  INTEGER,                  -- bakışta geçen süre (milisaniye)
            zaman    TEXT
        );
        """)
        _sutun_ekle(db, "haberler", "kategori", "TEXT")   # önceki sürümden kalan okul.db için


if __name__ == "__main__":
    tablo_olustur()
    print("Tablolar hazır:", DB_YOLU)
