/*
  İKİ İŞ YAPAR:
  1) GERİ SAYIM  : .geri-sayim ve .rozet-kalan öğelerini her saniye günceller.
  2) BAKIŞ TAKİBİ: bakılan noktayı (fare veya Gazepoint) alır, hangi bölgede (data-aoi) olduğunu
                   bulur, orada geçen süreyi biriktirir ve 3 saniyede bir sunucuya yollar.

  Tarama mantığı:
    her 50 ms'de bir "şu an nereye bakılıyor?" sorulur.
    O noktadaki öğenin en yakın [data-aoi] atası bulunur (örn. "geri-sayim").
    O bölgeye 50 ms eklenir. Böylece 60 Hz'lik bakış verisi yerine sabit bir örnekleme elde ederiz.
*/
(() => {
  "use strict";

  /* =======================  1) GERİ SAYIM  ======================= */
  const GUN = 864e5, SAAT = 36e5, DAKIKA = 6e4;

  function seviye(ms) {
    const saat = ms / SAAT;
    return saat < 48 ? "acil" : saat < 24 * 7 ? "uyari" : "sakin";
  }
  const iki = (n) => String(n).padStart(2, "0");

  function geriSayimGuncelle() {
    document.querySelectorAll(".geri-sayim").forEach((kutu) => {
      const ms = new Date(kutu.dataset.bitis).getTime() - Date.now();
      const sayac = kutu.querySelector(".sayac");
      if (ms <= 0) {
        kutu.classList.add("bitti");
        kutu.querySelector(".sayac-etiket").textContent = "";
        sayac.innerHTML = '<p class="bitti-yazi">Başvuru süresi doldu</p>';
        return;
      }
      kutu.dataset.seviye = seviye(ms);
      const g = Math.floor(ms / GUN);
      const s = Math.floor((ms % GUN) / SAAT);
      const d = Math.floor((ms % SAAT) / DAKIKA);
      const sn = Math.floor((ms % DAKIKA) / 1000);
      sayac.innerHTML =
        `<div class="birim"><b>${g}</b><span>gün</span></div>` +
        `<div class="birim"><b>${iki(s)}</b><span>saat</span></div>` +
        `<div class="birim"><b>${iki(d)}</b><span>dakika</span></div>` +
        `<div class="birim"><b>${iki(sn)}</b><span>saniye</span></div>`;
    });

    document.querySelectorAll(".rozet-kalan").forEach((r) => {
      const ms = new Date(r.dataset.bitis).getTime() - Date.now();
      if (ms <= 0) { r.textContent = "Süre doldu"; return; }
      r.dataset.seviye = seviye(ms);
      const g = Math.floor(ms / GUN);
      const s = Math.floor((ms % GUN) / SAAT);
      const d = Math.floor((ms % SAAT) / DAKIKA);
      r.textContent = g >= 1 ? `${g} gün ${s} sa kaldı` : `${s} sa ${iki(d)} dk kaldı`;
    });
  }
  geriSayimGuncelle();
  setInterval(geriSayimGuncelle, 1000);

  /* =======================  2) BAKIŞ TAKİBİ  ======================= */
  const params = new URLSearchParams(location.search);
  const MOD = params.get("mod") === "gaze" ? "gaze" : "fare";
  const ORNEK_MS = 50;          // bölge örnekleme aralığı
  const GONDER_MS = 3000;       // sunucuya toplu gönderme aralığı
  const ESKI_NOKTA_MS = 400;    // Gazepoint'ten bu kadar süredir veri yoksa "bakılmıyor" say

  let sonNokta = null;          // {x, y, t}  (x,y: tarayıcı penceresi içindeki piksel)
  const birikim = new Map();    // "haberId|aoi" -> toplam ms

  const nokta = document.getElementById("gaze-nokta");
  const durumIsik = document.getElementById("gaze-durum");
  const modYazi = document.getElementById("gaze-mod-yazi");
  const modDugme = document.getElementById("gaze-mod-degistir");
  const noktaDugme = document.getElementById("gaze-nokta-ac");

  function noktayiCiz() {
    if (!sonNokta || nokta.hidden) return;
    nokta.style.left = sonNokta.x + "px";
    nokta.style.top = sonNokta.y + "px";
  }

  /* --- Gazepoint koordinatını pencere pikseline çevir ---
     Gazepoint x,y değerini TÜM EKRANIN 0-1 oranı olarak verir.
     Tarayıcı penceresi ekranın bir parçası olduğundan pencerenin konumunu çıkarırız.
     (outerHeight - innerHeight) = adres çubuğu + sekme alanı yüksekliği.
     En doğru sonuç için tarayıcıyı F11 ile tam ekran yap: kenar payı sıfırlanır. */
  function ekrandanPencereye(nx, ny) {
    const yanPay = (window.outerWidth - window.innerWidth) / 2;
    const ustPay = window.outerHeight - window.innerHeight - yanPay;
    return {
      x: nx * screen.width - window.screenX - yanPay,
      y: ny * screen.height - window.screenY - ustPay,
    };
  }

  /* --- Veri kaynağı: fare veya Gazepoint --- */
  if (MOD === "gaze") {
    const akis = new EventSource("/gaze/stream");      // tarayıcı koparsa kendisi yeniden dener
    akis.onmessage = (e) => {
      const v = JSON.parse(e.data);
      const p = ekrandanPencereye(v.x, v.y);
      sonNokta = { x: p.x, y: p.y, t: Date.now() };
      noktayiCiz();
    };
  } else {
    document.addEventListener("mousemove", (e) => {
      sonNokta = { x: e.clientX, y: e.clientY, t: Date.now() };
      noktayiCiz();
    });
  }

  /* --- Her 50 ms: hangi bölgedeyiz? --- */
  setInterval(() => {
    if (document.hidden || !sonNokta) return;
    if (MOD === "gaze" && Date.now() - sonNokta.t > ESKI_NOKTA_MS) return;

    const oge = document.elementFromPoint(sonNokta.x, sonNokta.y);
    const bolge = oge && oge.closest("[data-aoi]");
    if (!bolge) return;

    const aoi = bolge.dataset.aoi;
    const haber = bolge.dataset.haberId || document.body.dataset.haberId || "";
    const anahtar = haber + "|" + aoi;
    birikim.set(anahtar, (birikim.get(anahtar) || 0) + ORNEK_MS);
  }, ORNEK_MS);

  /* --- Birikenleri sunucuya gönder --- */
  function gonder(liste) {
    if (!liste.length) return;
    fetch("/api/olay", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(liste),
      keepalive: true,                 // sayfa kapanırken bile gitsin
    }).catch(() => {});                // ağ hatası sayfayı bozmasın
  }

  function birikimiBosalt() {
    const liste = [];
    birikim.forEach((ms, anahtar) => {
      const [haber_id, aoi] = anahtar.split("|");
      liste.push({ haber_id, aoi, tur: "bakis", sure_ms: ms });
    });
    birikim.clear();
    gonder(liste);
  }
  setInterval(birikimiBosalt, GONDER_MS);
  document.addEventListener("visibilitychange", () => { if (document.hidden) birikimiBosalt(); });

  /* --- Başvuru butonuna tıklama --- */
  document.addEventListener("click", (e) => {
    const dugme = e.target.closest('[data-aoi="basvuru-butonu"]');
    if (!dugme) return;
    if (dugme.getAttribute("aria-disabled") === "true") e.preventDefault();
    gonder([{ haber_id: dugme.dataset.haberId, aoi: "basvuru-butonu", tur: "tiklama", sure_ms: 0 }]);
  });

  /* =======================  3) DURUM PANELİ  ======================= */
  modYazi.textContent = MOD === "gaze" ? "Gazepoint modu" : "Fare modu";
  modDugme.textContent = MOD === "gaze" ? "Fareye geç" : "Gazepoint'e geç";
  modDugme.addEventListener("click", () => {
    const yeni = new URLSearchParams(location.search);
    MOD === "gaze" ? yeni.delete("mod") : yeni.set("mod", "gaze");
    location.search = yeni.toString();
  });

  noktaDugme.addEventListener("click", () => {
    nokta.hidden = !nokta.hidden;
    noktaDugme.setAttribute("aria-pressed", String(!nokta.hidden));
    noktaDugme.textContent = nokta.hidden ? "Noktayı göster" : "Noktayı gizle";
    noktayiCiz();
  });

  async function gazeDurumSor() {
    try {
      const { durum } = await (await fetch("/gaze/durum")).json();
      durumIsik.className = "nokta " + (durum === "veri_akiyor" ? "canli" : durum === "bagli_veri_yok" ? "bekliyor" : "");
      durumIsik.title = { veri_akiyor: "Bakış verisi geliyor", bagli_veri_yok: "Gazepoint'e bağlı, veri yok (kamera/göz?)", bagli_degil: "Gazepoint'e bağlanılamadı" }[durum] || durum;
    } catch (_) { /* sunucu yoksa sessizce geç */ }
  }
  if (MOD === "gaze") { gazeDurumSor(); setInterval(gazeDurumSor, 2000); }
  else { durumIsik.className = "nokta canli"; durumIsik.title = "Fare modu: donanım gerekmez"; }
})();
