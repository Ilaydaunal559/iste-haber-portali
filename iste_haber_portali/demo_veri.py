"""
Örnek (SAHTE) haberler ekler. Amaç: gerçek siteden veri çekmeden önce
arayüzü, geri sayımı ve bakış takibini görebilmek.
Bu haberler 'demo://' ile başlayan url taşır; sayfada 'Örnek veri' etiketi görünür.

    python demo_veri.py          -> örnekleri ekle
    python demo_veri.py --temizle -> örnekleri sil
"""
import sys
from datetime import datetime, timedelta

from db import db_baglan, tablo_olustur


def an(**fark):
    return (datetime.now() + timedelta(**fark)).replace(microsecond=0).isoformat()


def ornekler():
    # (başlık, içerik, son_tarih)  -> son_tarih farklıdır ki 4 aciliyet düzeyi de görünsün
    return [
        ("TEKNOFEST takım başvuruları için son günler",
         "Takımını kuran öğrencilerimiz proje önerilerini çevrim içi sisteme yüklemeye devam ediyor.\n"
         "Başvuru için takım kaptanının öğrenci belgesi ve proje özeti sisteme yüklenmelidir.\n"
         "Danışman öğretim üyesi bilgisi başvuru sırasında zorunlu olarak istenmektedir.",
         an(hours=36)),
        ("Erasmus+ öğrenim hareketliliği başvuruları açıldı",
         "Uluslararası İlişkiler Ofisi, bahar dönemi öğrenim hareketliliği başvurularını duyurdu.\n"
         "Başvurular çevrim içi yapılacak; dil belgesi ve not dökümü yüklenmelidir.\n"
         "Bilgilendirme toplantısı fakülte konferans salonunda yapılacaktır.",
         an(days=5, hours=3)),
        ("Kariyer Günleri etkinliği için kayıtlar sürüyor",
         "Sektör temsilcileri kampüste öğrencilerle bir araya gelecek.\n"
         "Katılımcı sayısı salon kapasitesiyle sınırlı olduğundan kayıt formunun doldurulması gerekir.\n"
         "Etkinlik programı kısa süre içinde duyurulacaktır.",
         an(days=21)),
        ("Öğrenci kulüpleri tanıtım günü kampüs meydanında",
         "Kulüpler çalışmalarını standlarında tanıtacak; yeni üyeler kayıt olabilecek.\n"
         "Etkinlik hava şartlarına göre kapalı alanda da yapılabilecektir.",
         None),
        ("Bahar dönemi burs başvuruları sona erdi",
         "Başvuru sonuçları ilerleyen günlerde öğrenci bilgi sisteminde ilan edilecektir.\n"
         "Eksik belge nedeniyle değerlendirmeye alınamayan başvurular için ayrıca duyuru yapılacaktır.",
         an(days=-2)),
    ]


def ekle():
    tablo_olustur()
    bugun = datetime.now().date().isoformat()
    with db_baglan() as db:
        for i, (baslik, icerik, son) in enumerate(ornekler(), start=1):
            db.execute(
                """INSERT INTO haberler (baslik, icerik, url, tarih, son_tarih, tarih_kaynak)
                   VALUES (?, ?, ?, ?, ?, 'demo')
                   ON CONFLICT(url) DO UPDATE SET son_tarih = excluded.son_tarih""",
                (baslik, icerik, f"demo://ornek-{i}", bugun, son),
            )
    print("Örnek haberler eklendi.")


def temizle():
    with db_baglan() as db:
        db.execute("DELETE FROM haberler WHERE url LIKE 'demo://%'")
    print("Örnek haberler silindi.")


if __name__ == "__main__":
    temizle() if "--temizle" in sys.argv else ekle()
