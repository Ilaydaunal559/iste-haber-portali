"""
İSTE Haber Portalı: Flask uygulaması.

Çalıştır:  python app.py   ->  http://127.0.0.1:5000

Sayfalar:
    /                 haber listesi (süresi daralanlar üstte)
    /haber/<id>       haber detayı + geri sayım paneli
    /rapor            nörobilişim analizi: varyant başına bakış süresi / tıklama
    /gaze/stream      Gazepoint bakış verisi (SSE)
    /gaze/durum       köprü durumu
    /api/olay         tarayıcıdan gelen bakış/tıklama olaylarını kaydeder

DENEY TASARIMI (A/B):
    Her ziyaretçi rastgele bir varyanta atanır:
      'kitlik'  -> geri sayım ve aciliyet rozetleri görünür
      'kontrol' -> aynı bilgi sade metin olarak görünür (sadece "Son başvuru: 15.11.2026")
    ?v=kitlik veya ?v=kontrol ile elle seçilebilir.
"""
import json
import os
import queue
import random
import uuid
from datetime import datetime

from flask import (Flask, Response, abort, jsonify, render_template, request,
                   session, stream_with_context)

from db import db_baglan, tablo_olustur
from gaze_bridge import kopru

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "gelistirme-anahtari-degistir")

IZIN_VERILEN_AOI = {"baslik", "icerik", "geri-sayim", "basvuru-butonu", "kart", "tarih-bilgisi", "pano"}


# ---------------------------------------------------------------------------
# Oturum ve varyant atama
# ---------------------------------------------------------------------------
@app.before_request
def oturum_hazirla():
    if request.endpoint == "static":
        return
    if "oturum" not in session:
        session["oturum"] = uuid.uuid4().hex                      # anonim kimlik
        session["varyant"] = random.choice(["kitlik", "kontrol"])  # rastgele grup
    secilen = request.args.get("v")
    if secilen in ("kitlik", "kontrol"):
        session["varyant"] = secilen


@app.context_processor
def sablon_degiskenleri():
    return {"varyant": session.get("varyant", "kitlik")}


@app.template_filter("tr_tarih")
def tr_tarih(iso):
    """2026-11-15T23:59:00 -> 15.11.2026"""
    if not iso:
        return ""
    return datetime.fromisoformat(iso).strftime("%d.%m.%Y")


# ---------------------------------------------------------------------------
# Sayfalar
# ---------------------------------------------------------------------------
@app.route("/")
def anasayfa():
    simdi = datetime.now().isoformat(timespec="seconds")
    with db_baglan() as db:
        # Süresi dolmamış başvurular: en yakın bitiş en üstte (departure board mantığı)
        panoda = db.execute(
            "SELECT * FROM haberler WHERE son_tarih IS NOT NULL AND son_tarih >= ? "
            "ORDER BY son_tarih ASC", (simdi,)).fetchall()
        digerleri = db.execute(
            "SELECT * FROM haberler WHERE son_tarih IS NULL OR son_tarih < ? "
            "ORDER BY tarih DESC, id DESC", (simdi,)).fetchall()
    return render_template("index.html", panoda=panoda, digerleri=digerleri)


@app.route("/haber/<int:haber_id>")
def haber_detay(haber_id):
    with db_baglan() as db:
        h = db.execute("SELECT * FROM haberler WHERE id = ?", (haber_id,)).fetchone()
    if h is None:
        abort(404)
    paragraflar = [p for p in (h["icerik"] or "").split("\n") if p.strip()]
    return render_template("haber.html", h=h, paragraflar=paragraflar)


@app.route("/rapor")
def rapor():
    with db_baglan() as db:
        ozet = db.execute(
            """SELECT varyant,
                      COUNT(DISTINCT oturum) AS kisi,
                      COALESCE(SUM(tur = 'tiklama'), 0) AS tiklama
               FROM olaylar GROUP BY varyant""").fetchall()
        bolgeler = db.execute(
            """SELECT varyant, aoi,
                      SUM(sure_ms) / 1000.0 AS saniye,
                      COUNT(DISTINCT oturum) AS kisi
               FROM olaylar WHERE tur = 'bakis'
               GROUP BY varyant, aoi ORDER BY varyant, saniye DESC""").fetchall()
    return render_template("rapor.html", ozet=ozet, bolgeler=bolgeler)


# ---------------------------------------------------------------------------
# Olay kaydı (tarayıcı -> sunucu)
# ---------------------------------------------------------------------------
@app.post("/api/olay")
def olay_kaydet():
    gelen = request.get_json(force=True, silent=True)
    if not isinstance(gelen, list):
        return jsonify(ok=False, hata="liste bekleniyordu"), 400

    zaman = datetime.now().isoformat(timespec="seconds")
    satirlar = []
    for o in gelen[:100]:                                   # tek istekte en fazla 100 olay
        try:
            aoi = str(o.get("aoi", ""))[:40]
            tur = "tiklama" if o.get("tur") == "tiklama" else "bakis"
            sure = int(o.get("sure_ms", 0))
            hid = o.get("haber_id")
            hid = int(hid) if hid not in (None, "") else None
        except (AttributeError, TypeError, ValueError):
            continue                                        # bozuk olayı atla
        if aoi not in IZIN_VERILEN_AOI or not (0 <= sure <= 60000):
            continue
        satirlar.append((session.get("oturum"), session.get("varyant"), hid, aoi, tur, sure, zaman))

    if satirlar:
        with db_baglan() as db:
            db.executemany(
                "INSERT INTO olaylar (oturum, varyant, haber_id, aoi, tur, sure_ms, zaman) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", satirlar)
    return jsonify(ok=True, kayit=len(satirlar))


# ---------------------------------------------------------------------------
# Gazepoint verisi (sunucu -> tarayıcı) : Server-Sent Events
# ---------------------------------------------------------------------------
@app.get("/gaze/stream")
def gaze_akis():
    kuyruk = kopru.abone_ol()

    def uret():
        try:
            while True:
                try:
                    nokta = kuyruk.get(timeout=15)
                    yield f"data: {json.dumps(nokta)}\n\n"
                except queue.Empty:
                    yield ": canli\n\n"                     # bağlantı kopmasın diye nabız
        finally:
            kopru.abonelik_iptal(kuyruk)                    # sekme kapanınca temizle

    return Response(stream_with_context(uret()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/gaze/durum")
def gaze_durum():
    return jsonify(durum=kopru.durum)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tablo_olustur()
    kopru.start()                       # Gazepoint dinleyicisini arka planda başlat
    # use_reloader=False: debug yeniden yükleyicisi uygulamayı 2 kez çalıştırıp
    # köprüyü çift başlatırdı.
    app.run(debug=True, use_reloader=False, threaded=True)
