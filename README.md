# claude-cost

Claude Code session'larının **ne kadar sürdüğünü** ve **plan bedelinin ne kadarına
denk geldiğini** raporlayan bir Claude Code plugin'i. Freelance faturalama için
"duvar saati" ile "aktif çalışma" ayrımını da verir.

Çalışılan iş repolarına hiçbir dosya eklemez — veri zaten
`~/.claude/projects/<proje>/<session-id>.jsonl` içinde mevcuttur.

## Kurulum

Her makinede **tek seferlik**:

```
/plugin marketplace add https://github.com/cemozay/claude-cost.git
/plugin install cost@claude-cost
```

Sonrasında her değişiklik `/plugin update` ile otomatik gelir; elle dosya
kopyalamak yok. (Claude Code ayarları makineler arası senkronize olmadığı için
yeni bir makinede bu iki komut kaçınılmazdır.)

**Gereksinim:** Python 3.8+ (yalnızca stdlib). Linux sunucularında genelde hazır
gelir. Windows'ta yoksa: `winget install Python.Python.3.12`

## Kullanım

Claude Code içinde `/cost` yaz — ya da "bu session ne kadar tuttu", "bu ay ne
kadar harcadım" gibi bir şey sor.

Doğrudan komut satırından da çalışır (ama gerekmez — hepsi `/cost` üzerinden
doğal dille yapılabilir):

```
python scripts/claude_cost.py                     # o anki session
python scripts/claude_cost.py --month             # bu donemin ozeti
python scripts/claude_cost.py --month 2026-07     # o ayda BASLAYAN donem
python scripts/claude_cost.py --month 2026-08-08  # tam donem anahtari (yalnizca cycle gununde)

python scripts/claude_cost.py --tag-list --untagged                 # etiketsizler
python scripts/claude_cost.py --tag <id> dahil                      # tek etiket
python scripts/claude_cost.py --untag <id>                          # etiketi kaldir
python scripts/claude_cost.py --tag-project "*Frames*" dahil --yes  # toplu

python scripts/claude_cost.py --set-tracking-start 2026-08  # takip baslangici
python scripts/claude_cost.py --set-billing-day 8            # fatura donemi 8'den 8'e (1-28)
python scripts/claude_cost.py --suggest-baseline            # taban tavsiyesi (yazmaz)
python scripts/claude_cost.py --set-baseline 1500           # tabani yaz

python scripts/claude_cost.py --export out.json --machine workcube  # disa aktar

python scripts/claude_cost.py --idle-gap 10       # ara esigini gecici ez (dk)
python scripts/claude_cost.py --json              # makine okunur cikti
python scripts/claude_cost.py --selftest          # 45 dogrulama kontrolu
```

## Örnek çıktı

```
Session:   Brainstorm superpowers with Velvet Creek
Proje:     C:\Users\cemyu  (HEAD)
Etiket:    dahil
Baslangic: 16 Agustos 2026 18:34   Bitis: 18 Aug 2026 17:56

Sure
  Duvar saati : 47sa 21dk 45sn
  Aktif       : 3sa 22dk 59sn   (43sa 58dk 47sn bosluk haric, esik 5dk)
    haric tutulan aralar (17 adet):
      17 Aug 13:40  13sa 20dk 35sn
      18 Aug 04:02  12sa 16dk 35sn
      17 Aug 02:16  11sa 07dk 45sn
      16 Aug 19:05   5sa 26dk 03sn
      18 Aug 03:27       16dk 00sn
      18 Aug 16:59       12dk 14sn
      ... 11 ara daha

Token (requestId ile tekillestirilmis, 245 istek)
  input                     470      cache write 5dk               0
  output                335.293      cache write 1sa       4.605.626
  cache read        139.878.536

A) API-karsiligi maliyet
  claude-opus-5           $124.38
  <synthetic>               $0.00
  TOPLAM                  $124.38

B) Plan maliyeti (sabit oran)
  Taban          : $1,500.00/ay   (2026-08-18 tarihinde elle konuldu)
  Bu session     : $124.38 / $1,500.00 x $20.00  =  $1.66   [SABIT]
```

Aylık özet:

```
Agustos 2026  (ay ici, gecici)

  Dahil     :  34 session    $1,672.89
  Taban     : $1,500.00/ay  ->  kullanim %111.5  ->  plan maliyeti $22.31
  Etiketsiz :   4 session      $262.91   (hesaba KATILMADI)
```

## Molalar: `Duvar saati` vs `Aktif`

Limit yediğinde, işin çıktığında ya da bilgisayarı bırakıp gittiğinde geçen süre
**faturalanacak sürede olmamalı.** `Aktif` tam olarak bunu verir:

- **Duvar saati** — ilk kayıttan son kayda kadar geçen ham süre.
- **Aktif** — eşiği (varsayılan 5 dk) aşan her boşluk düşülmüş hali.
  **Faturalanacak rakam budur.**

Hangi araların düşüldüğü tek tek listelenir, çünkü müşteriye verilen rakamın
savunulabilir olması gerekir. Eşik sana uymuyorsa dene:

```
python scripts/claude_cost.py --idle-gap 2     # daha sıkı: kısa duraklar da düşülür
python scripts/claude_cost.py --idle-gap 15    # daha gevşek: sadece uzun molalar
python scripts/claude_cost.py --set-idle-gap 10  # beğendiğini kalıcı yap
```

### Eşik neden 5 dakika?

Rastgele seçilmedi, veriye bakılarak seçildi. Bu makinedeki 85 session'da
ardışık `assistant` kayıtları arasındaki **10.811** aralık ölçüldü: en uzunu
**2.92 dakika**, 5 dakikayı aşan **sıfır**. Yani model tek bir turda hiçbir zaman
5 dakika sessiz kalmıyor — dolayısıyla 5 dakikalık eşik gerçek çalışmayı asla
yanlışlıkla "mola" saymıyor. Eşiği 3 dakikanın altına çekersen bu güvence kalkar.

> Boşluğun yönüne (kim son hamleyi yaptı) bakan bir sınıflandırma denendi ve
> **çürütüldü**: `kullanıcı → model` boşluklarının süreleri 258–586 dakika
> çıkıyor. Model 10 saat düşünmez; onlar da kullanıcı arası. Yön bilgisi mola
> tespiti için kullanışsız, süre eşiği ise yeterli.

## İki maliyet modeli neden var?

- **A) API-karşılığı maliyet** — session'ın token kullanımının API liste
  fiyatındaki karşılığı. Abonelik planında bu tutar tahsil edilmez; session'ın
  "ağırlığını" ölçen mutlak bir rakamdır.
- **B) Plan maliyeti (sabit oran)** — `session_API_karşılığı ÷ TABAN × plan`.

**Neden taban?** Önceki sürümde payda ayın o ana kadarki toplamıydı; ay
ilerledikçe aynı session'ın rakamı düşüyordu. Session bitiminde sabit bir sayı
alınamıyor, oturumlar ve aylar karşılaştırılamıyordu.

Bir rakam **aynı anda** hem session bitince sabitlenip hem ay sonunda toplamı
tam plan tutarını edemez — session biterken kaç oturum daha açılacağı bilinmez.
**Sabitlik seçildi.** Aylık toplamın plan tutarından sapması bilgidir:
%130 "planı yoğun kullanıyorum", %40 "az kullanıyorum".

Taban `--suggest-baseline` ile önerilir ama **rakamı sen seçersin**; araç
kendiliğinden yazmaz. Taban yoksa rapor hiç rakam basmaz, durumu bildirir.

## Hangi session'lar sayılıyor

Claude hem müşteri işlerinde hem kendi projelerinde kullanılıyor. Yalnızca
**etiketlenmiş** oturumlar plan maliyetine girer:

| Etiket | Anlamı |
|---|---|
| `dahil` | müşteri işi, hesaba katılır |
| `haric` | kendi projen, katılmaz |
| **etiketsiz** | üçüncü durum: ne dahil ne hariç, raporda **ayrı satırda** görünür |

Etiketsiz oturumlar sessizce bir tarafa yazılmaz — etiketlemeyi unutursan
rakamın eksik olduğunu görürsün. `tracking_start_month` ile takibin başladığı
ay belirlenir; öncesindeki oturumlar hiç görünmez.

**Fatura dönemi takvim ayı olmak zorunda değil.** `billing_cycle_day`
1'den farklıysa (ör. 8), "ay" aslında `8 Ağustos – 8 Eylül` gibi bir dönemdir.
Ayın 7'sinde biten büyük bir harcama önceki döneme, 8'inde biteni yeni döneme
düşer — takvim ayına göre gruplarsan bu ikisi karışır.

Etiketlemeyi elle yapmazsın: `/cost` içinden "etiketsizleri göster" dersin,
numaralı liste gelir, "1 ve 3 dahil" dersin. UUID görmezsin.

## Çok makineli kullanım

Her makinede `--export`, dosyaları ana makinede `~/.claude/cost-imports/`
klasörüne koy. Rapor hepsini birleştirir, `session_id` ile tekilleştirir
(yerel veri kazanır). Export'ta **ham konuşma yoktur** — yalnızca süre/token/
maliyet özeti; ölçülen boyut 38 oturum için 19 KB.

## Neden `requestId` ile tekilleştirme?

Tek bir API yanıtı, içerdiği **her content block için ayrı bir JSONL satırı**
olarak yazılır (`thinking` / `text` / `tool_use`) ve her satır **aynı `usage`
nesnesini** taşır. Naif toplama yaparsan tüm rakamlar şişer — ölçülen oran
bu makinede **3.01×** idi.

`--selftest` bu varsayımı yerel verine karşı **kanıtlar**: aynı `requestId`'ye
sahip tüm satırların `usage` nesnelerinin birebir aynı olduğunu assert eder,
sonra naif/dedup oranını basar. Sabit, makineye özel rakamlara dayanmaz.

## Config

`~/.claude/cost-config.json` — ilk çalıştırmada otomatik oluşur, **repoya girmez**
(plan tutarı makineye/aboneliğe özel kalabilir).

```json
{
  "plan": {"amount": 20.0, "currency": "USD", "label": "Pro"},
  "idle_gap_seconds": 300,
  "tracking_start_month": "2026-08",
  "billing_cycle_day": 1,
  "baseline_monthly_api_cost": 1500.0,
  "baseline_source": {"set_at": "2026-08-18T09:00:00+00:00", "method": "manual"},
  "pricing_per_mtok": {
    "claude-opus-5": {"input": 5.0, "output": 25.0}
  },
  "cache_multipliers": {"write_5m": 1.25, "write_1h": 2.0, "read": 0.1}
}
```

- **Fiyatlar config'de tutulur** ki fiyat değişince kod değişmesin.
- Cache maliyeti input fiyatının çarpanı olarak hesaplanır (API'nin gerçek modeli budur):
  5dk yazma 1.25×, 1sa yazma 2.0×, okuma 0.1×.
- `idle_gap_seconds` — "aktif süre" hesabında bu eşiği aşan boşluklar düşülür.
- `tracking_start_month` — takibin başladığı ay; öncesi hiç görünmez.
- `billing_cycle_day` — fatura döneminin başladığı gün, **1-28** arası
  (varsayılan `1` = takvim ayı, tam geriye uyumlu). `8` gibi bir değerle
  dönemler `8 Ağustos - 8 Eylül` şeklinde takvim ayını aşabilir.
  `--set-billing-day <gün>` ile kalıcı yazılır; 1-28 dışı bir değer reddedilir
  (her ayda o gün bulunsun diye — 29/30/31 bazı aylarda yok).
- `baseline_monthly_api_cost` — **dondurulmuş taban.** Kendiliğinden asla
  değişmez; yalnızca `--set-baseline` yazar. `baseline_source` nereden geldiğini
  kaydeder ki aylar sonra rakam açıklanabilsin.
- Etiketler ayrı dosyada: `~/.claude/cost-tags.json` (o da repoya girmez).
- **Fiyat tablosunda olmayan model sessizce 0 sayılmaz**: `WARN` basılır ve
  rapora `EKSIK` notu düşer.

### Fiyat notu

Tablo API **liste** fiyatlarıdır. Claude Sonnet 5'in 2026-08-31'e kadar geçerli
bir tanıtım fiyatı vardır ($2/$10 per MTok); config liste fiyatını ($3/$15)
kullanır. Tanıtım fiyatını yansıtmak istersen config'den değiştir.

## Kapsam dışı (bilinçli)

- Otomatik SessionEnd hook ve statusline entegrasyonu — script bunları sonradan
  besleyebilecek şekilde (`--json`) tasarlandı.
- Gerçek Anthropic fatura/kullanım API'sine bağlanma — abonelik planında token
  bazlı tutar zaten nominal.

## Lisans

MIT
