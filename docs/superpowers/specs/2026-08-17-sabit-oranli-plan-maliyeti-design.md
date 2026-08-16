# Sabit oranlı plan maliyeti + session etiketleme + çok makineli toplama

Tarih: 2026-08-17
Durum: tasarım onaylandı, uygulama bekliyor
Hedef sürüm: claude-cost 2.0.0

## Problem

v1.1.0'daki plan payı modeli `session_maliyeti / ay_toplamı × plan_tutarı`
şeklinde çalışıyor. Bunun iki kusuru var:

1. **Rakam geriye dönük değişiyor.** Ay ilerledikçe payda büyüyor, aynı
   session'ın rakamı düşüyor. Session bitiminde sabit bir sayı alınamıyor,
   dolayısıyla oturumlar ve aylar arasında karşılaştırma yapılamıyor.
2. **Kapsam yanlış.** Tüm session'lar hesaba giriyor. Kullanıcı hem freelance
   müşteri işleri hem kendi projeleri için Claude kullanıyor; ayrıca birden
   çok makinede (Windows + SSH ile bağlanılan Linux sunucular) çalışıyor.
   Tek makinenin tüm session'ları üzerinden hesaplanan taban yanlış.

Ek olarak `-> Pro plan $20'nin %172.7'si` satırı yanıltıcı: planın aşıldığını
ima ediyor, oysa abonelikte böyle bir şey yok.

### Ölçülen veri (85 session, 3 ay)

Aylık API-karşılığı toplamlar **6.7 kat** oynuyor:

| Ay | Session | API-karşılığı | Aktif saat |
|---|---|---|---|
| 2026-06 | 11 | $360.68 | 12.3 |
| 2026-07 | 41 | $2.432.66 | 56.5 |
| 2026-08 | 33 | $1.598.83 (yarım) | 30.5 |

Proje dağılımı (tüketimin %99'u iki projede):

| Proje | Session | API-karşılığı |
|---|---|---|
| `Desktop\Files\Projects\Frames` | 54 | $2.804.24 |
| `Desktop\Files\Projects\FramesWeb` (+alt) | 28 | $1.544.78 |
| `C:\Users\cemyu` | 1 | $37.50 |
| `c:\dev\wappi` | 2 | $7.60 |

## Kabul edilen temel kısıt

Bir rakam **aynı anda** şu ikisini sağlayamaz:

- session bitince sabitlenip bir daha değişmemek,
- ay sonunda toplamı tam olarak plan tutarını (=$20) etmek.

Session bitiminde kaç session daha açılacağı bilinmediği için, toplamı $20'ye
çiviyen her model zorunlu olarak geriye dönük düzeltme yapar. **Sabitlik
seçildi**, aylık toplamın $20'den sapması kabul edildi. Sapma bilgi taşıyor:
%130 "planı fazlasıyla kullanıyorum", %40 "az kullanıyorum" demek.

## Alınan kararlar

| Karar | Seçim | Gerekçe |
|---|---|---|
| Rakamın amacı | Kendi maliyet takibi | Üçüncü tarafa gösterilmiyor; **karşılaştırılabilirlik** kritik |
| Sabit rakam neye orantılı | Token tüketimi (API-karşılığı) | Ağır oturum pahalı çıksın; süre bazlı model token yoğunluğunu görmezden gelirdi |
| Taban nasıl belirlenir | Elle set edilir; araç yalnızca **tavsiye** verir | Geçmiş aylar hesaba katılmayacak (kullanıcı kararı). Takip bu aydan başlıyor, dolayısıyla türetilecek tamamlanmış ay yok. Rakam kullanıcının, dondurulmuş. |
| Takip başlangıcı | `tracking_start_month` config | "Bu aydan itibaren başlayalım" — öncesindeki 52 oturum hiç görünmesin, etiketlenmesi istenmesin |
| Session seçimi | İkili etiket (dahil/hariç), session bazlı | Klasör yolu yetmiyor: aynı klasörde iki tür iş olabiliyor |
| Etiketsizler | Üçüncü durum | Sessizce yanlış rakam üretmemek; araçtaki "bilinmeyen model" ilkesinin aynısı |
| Çok makine | Elle export/import | Sıfır altyapı, daemon yok, zamanlanmış iş yok |

## Veri modeli

### Etiket deposu — `~/.claude/cost-tags.json`

Ana makinede **tek** dosya. Session UUID'leri makineden bağımsız olduğu için
uzak makinelerdeki oturumlar da buradan etiketlenir.

```json
{
  "version": 1,
  "tags": {
    "88019e6d-fced-4433-b4cf-6483e88857ef": true,
    "a8c0edcc-c78f-4b6c-9195-6413832f8e81": false
  }
}
```

`true` = dahil, `false` = hariç, **anahtar yok** = etiketsiz.

Repoya girmez, her makinede yereldir (ana makinede tutulur).

### Export formatı — `--export <dosya>`

Ham transcript **gönderilmez**; yalnızca session başına özet. Hem dosya küçük
kalır hem müşteri kodu / konuşma içeriği makine dışına çıkmaz.

```json
{
  "version": 1,
  "machine": "workcube",
  "exported_at": "2026-08-17T09:12:00+00:00",
  "sessions": [
    {
      "session_id": "…",
      "start": "2026-08-16T15:34:24.402000+00:00",
      "end":   "2026-08-16T21:59:15.100000+00:00",
      "active_seconds": 2588.0,
      "wall_seconds": 23091.0,
      "cwd": "/home/cemyu/workcube",
      "gitBranch": "main",
      "title": "…",
      "request_count": 86,
      "tokens": {"input": 165, "output": 89156, "cache_read": 36589795,
                 "cache_write_5m": 0, "cache_write_1h": 1402444},
      "cost": 34.55,
      "per_model": {"claude-opus-5": {"cost": 34.55, "requests": 86}},
      "unknown_models": []
    }
  ]
}
```

Zaman damgaları **daima UTC ISO**; rapor yerel saatte gösterir. Makine adı
`--machine <ad>` ile verilir, verilmezse `socket.gethostname()`.

### Birleştirme — `~/.claude/cost-imports/*.json`

Araç yerel transcript'leri **ve** bu klasördeki tüm export dosyalarını okur.
`session_id` ile tekilleştirir; **yerel veri kazanır** (ana makinenin kendi
export'u da klasöre düşerse çift sayılmaz).

Bozuk veya sürümü bilinmeyen import dosyası atlanır ve dosya adı + sebep
uyarı olarak basılır — sessizce yutulmaz.

## CLI arayüzü

Mevcut bayraklar korunur. Eklenenler:

```
--export <dosya>            # bu makinenin ozetini yaz
--machine <ad>              # export'a yazilacak makine adi (varsayilan: hostname)

--tag <session-id> dahil|haric      # tek session etiketle
--tag-project "<desen>" dahil|haric # cwd desenine uyan session'lari toplu etiketle
--tag-list [--untagged]             # session'lari etiketleriyle listele
--untag <session-id>                # etiketi kaldir (etiketsize dondur)

--set-baseline <tutar>      # tabani yaz (birincil yol)
--suggest-baseline          # tavsiye: dahil edilenlerin bu ayki toplamini goster,
                            #   HICBIR SEY YAZMAZ
--set-tracking-start <YYYY-MM>  # takip baslangic ayi
```

`--tag-project` deseni, session'ın **`cwd` alanına** uygulanan bir
`fnmatch` glob'udur (büyük/küçük harf duyarsız — Windows yolları için gerekli).
Örnek: `"*Projects\Frames*"`. Eşleşen her session **tek tek** etiket deposuna
yazılır; desen saklanmaz, yani sonradan yeni açılan bir session otomatik
etiketlenmez.

Bu yalnızca **tohumlama kolaylığı**. Nihai kayıt her zaman session bazındadır
ve tek tek `--tag` ile ezilebilir. 85 mevcut oturumu elle işaretleme yükünü
kaldırmak için var.

Komut, yazmadan önce kaç session'ı etkileyeceğini gösterir ve onay ister
(`--yes` ile atlanabilir) — 54 session'ı yanlışlıkla etiketlemek kolay olmasın.

## Takip başlangıcı

```json
{"tracking_start_month": "2026-08"}
```

Bu aydan **önce** başlayan session'lar tamamen yok sayılır: rapora girmez,
`--tag-list` içinde listelenmez, "etiketsiz" uyarısı üretmez. Kullanıcı
takibe bu aydan başlıyor; öncesindeki 52 oturum (Haziran+Temmuz) geriye dönük
etiketlenmeyecek.

Set edilmemişse tüm session'lar kapsamdadır (geriye uyumluluk).

## Taban (baseline)

Config'e eklenir ve **kendiliğinden asla değişmez**:

```json
{
  "baseline_monthly_api_cost": 1200.0,
  "baseline_source": {
    "set_at": "2026-08-17T10:00:00+00:00",
    "method": "manual",
    "note": "Agustos'un ilk yarisindaki dahil edilen oturumlara bakilarak secildi"
  }
}
```

`baseline_source` zorunlu: aylar sonra "$0.46 nereden çıktı" sorusunun cevabı
config'de dursun.

### Neden geçmişten türetilmiyor

Takip bu aydan başlıyor, dolayısıyla türetilecek tamamlanmış ay yok. Geçmiş
aylar (Haziran $360, Temmuz $2.432) kullanıcı kararıyla kapsam dışı. Bu
nedenle **`--seed-baseline` gibi bir otomatik türetme komutu yok** — olmayan
veriden rakam üretmek, aracın "sessizce yanlış sayı basma" ilkesine aykırı
olurdu.

### `--suggest-baseline` (yalnızca tavsiye)

Hiçbir şey yazmaz, yalnızca karar vermeye yardım eder:

```
Takip baslangici: 2026-08

  Dahil edilen (2026-08, su ana kadar) : 12 session   $684,20
  Ayin gecen kismi                     : 17/31 gun
  Dogrusal izdüsüm (tam ay)             : ~$1.247,00

  Etiketsiz: 4 session  $196,73   <-- once bunlari etiketle, sayi degisir

Tabani sen seciyorsun. Ornek:
  claude_cost.py --set-baseline 1250
```

Doğrusal izdüşüm **tahmindir** ve öyle etiketlenir: kullanım düzensiz
olduğunda yanıltır. Karar kullanıcınındır; araç rakamı kendisi yazmaz.

Etiketsiz oturum varken uyarı basar — çünkü onlar etiketlenince "dahil"
toplamı değişir ve taban yanlış seçilmiş olur.

## Formül

Yalnızca **dahil** etiketli session'lar için:

```
sabit_plan_maliyeti = session_API_karşılığı ÷ TABAN × plan_tutarı
```

Bu rakam session bitiminde sabitlenir. Sonradan açılan oturumlar onu
etkilemez — çünkü payda artık ay toplamı değil, dondurulmuş taban.

## Rapor çıktıları

### Session raporu

`Süre` ve `Token` bölümleri değişmez. Değişenler:

```
Etiket:    dahil

A) API-karsiligi maliyet
  claude-opus-5            $34.55
  TOPLAM                   $34.55

B) Plan maliyeti (sabit oran)
  Taban          : $1.500,00/ay   (2026-08-17'de Haz+Tem'den turetildi)
  Bu session     : $34,55 / $1.500,00 x $20,00  =  $0,46   [SABIT]
```

- `-> Pro plan $20'nin %172.7'si` satırı **kaldırılır** (yanıltıcı).
- Session `hariç` ise B bölümü yerine `Bu session haric tutulmus.` yazılır.
- Session `etiketsiz` ise B bölümü yerine etiketleme komutu önerilir.

### Aylık rapor

Ay payı yerine kullanım oranı:

```
Agustos 2026
  Dahil     : 28 session   $1.402,10
  Taban     : $1.500,00/ay  ->  kullanim %93.5  ->  plan maliyeti $18,70
  Etiketsiz : 4 session   $196,73   (hesaba KATILMADI)
  Haric     : 1 session   $37,50
```

Aylık toplam artık plan tutarına çivilenmez.

### Kaldırılan kod

`plan_share()` ve session raporundaki `B) Plan payi` bölümü kaldırılır;
yerine sabit oranlı hesap gelir. `month_totals()` etiket kırılımı döndürecek
şekilde genişletilir.

## Hata durumları

| Durum | Davranış |
|---|---|
| Taban set edilmemiş | Rapor **durur**, `--suggest-baseline` + `--set-baseline` önerir. Uydurma varsayılan yok. |
| Takip başlangıcından önceki session | Tamamen yok sayılır (listelenmez, uyarı üretmez) |
| `--set-baseline` ≤ 0 | Hata |
| Import dosyası bozuk / sürüm bilinmiyor | Dosya atlanır, adı ve sebep uyarı olarak basılır |
| Aynı session yerelde ve import'ta | Yerel kazanır, çift sayılmaz |
| Etiket dosyası yok | Hepsi etiketsiz; rapor bunu açıkça yazar |
| `--tag` bilinmeyen session-id | Hata, sessizce kaydetmez |
| Farklı makine farklı saat dilimi | Export UTC ISO, rapor yerel saat |

## Doğrulama

`--selftest` mevcut 15 kontrole ek olarak:

16. **Etiket turu** — `--tag` sonrası dosyadan okunan değer aynı; `--untag`
    sonrası etiketsize dönüyor.
17. **Merge tekilleştirme** — aynı `session_id` iki import dosyasında; toplam
    bir kez sayılıyor.
18. **Export→import turu** — export edilip geri okunan session'ın maliyeti,
    yerelde hesaplanana eşit.
19. **Sabit oran değişmezliği** — aynı session, ay toplamı yapay olarak iki
    katına çıkarılıp yeniden hesaplanıyor; **rakam değişmemeli.** Bu kontrol
    v1.1.0 modelinde başarısız olurdu; yeni modelde geçmesi zorunlu.
20. **Etiketsiz izolasyonu** — etiketsiz session'lar dahil toplamına girmiyor,
    ayrı satırda görünüyor.
21. **Taban yokluğu** — taban set edilmemişken rapor sayı üretmiyor, hata veriyor.
22. **Takip başlangıcı** — başlangıç ayından önceki session'lar hiçbir toplama
    girmiyor ve etiketsiz listesinde çıkmıyor.

19 numara bu tasarımın varlık sebebidir; asıl şikâyetin bir daha oluşamayacağını
her çalıştırmada kanıtlar.

## Uygulama sırası

1. Etiket deposu (`cost-tags.json`) + `--tag` / `--untag` / `--tag-list`
2. `tracking_start_month` + `--set-tracking-start` (kapsam filtresi)
3. `--tag-project` toplu tohumlama
4. Taban: `--set-baseline` / `--suggest-baseline` + `baseline_source`
5. Sabit oran formülü + session raporunun B bölümü
6. Aylık raporun etiket kırılımı + kullanım oranı
7. `--export` / `--machine` + `cost-imports/` birleştirme
8. Selftest 16-22
9. README + SKILL.md güncelle, sürüm 2.0.0

Adım 1-6 tek makinede çalışan bitmiş bir ürün verir; 7 kapsamı genişletir.

## Kapsam dışı (bilinçli)

- Otomatik senkronizasyon (git/rsync/SSH çekme) — elle export/import seçildi.
- Geçmişten taban türetme (`--seed-baseline`) — takip bu aydan başlıyor,
  türetilecek tamamlanmış ay yok. İleride birkaç tam ay biriktiğinde yeniden
  değerlendirilebilir; o zaman gerçek veri üzerinden tasarlanır.
- Müşteri/proje bazlı etiket (serbest metin) — ikili etiket seçildi. Etiket
  deposunun şeması ileride genişletmeye izin verecek şekilde sürümlü.
- Gerçek Anthropic fatura API'si — abonelikte token bazlı tutar nominal.
- Süre bazlı (saatlik) maliyet modeli — token bazlı seçildi.
