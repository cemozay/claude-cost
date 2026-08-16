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

Doğrudan komut satırından da çalışır:

```
python scripts/claude_cost.py                  # o anki session
python scripts/claude_cost.py --session <id>   # belirli session
python scripts/claude_cost.py --month          # bu ayın özeti (tüm projeler)
python scripts/claude_cost.py --month 2026-07  # belirli ay
python scripts/claude_cost.py --plan 100       # plan tutarını bu çalıştırma için ez
python scripts/claude_cost.py --set-plan 100   # config'e kalıcı yaz
python scripts/claude_cost.py --idle-gap 10    # ara eşiğini bu çalıştırma için ez (dk)
python scripts/claude_cost.py --set-idle-gap 10 # ara eşiğini kalıcı yaz (dk)
python scripts/claude_cost.py --json           # makine okunur çıktı
python scripts/claude_cost.py --selftest       # doğrulama modu
```

## Örnek çıktı

```
Session:   Brainstorm superpowers with Velvet Creek
Proje:     C:\Users\cemyu  (HEAD)
Baslangic: 16 Agustos 2026 18:34   Bitis: 18:50

Sure
  Duvar saati : 6sa 09dk 09sn
  Aktif       : 27dk 26sn   (5sa 41dk 43sn bosluk haric, esik 5dk)
    haric tutulan aralar (3 adet):
      16 Aug 19:05   5sa 26dk 03sn
      16 Aug 18:55        9dk 52sn
      16 Aug 18:34        5dk 48sn

Token (requestId ile tekillestirilmis, 22 istek)
  input                      43      cache write 5dk               0
  output                 37.600      cache write 1sa         417.842
  cache read          8.135.769

A) API-karsiligi maliyet
  claude-opus-5             $9.19
  TOPLAM                    $9.19
                                   -> Pro plan $20.00 tutarinin %45.9'i

B) Plan payi - Agustos 2026  (ay ici, gecici)
  Bu ay: 33 session, toplam API-karsiligi $1,572.48
  Bu session payi: %0.6  ->  $0.12 / $20.00
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
  fiyatındaki karşılığı. Abonelik planında bu tutar gerçekte tahsil edilmez;
  session'ın "ağırlığını" ölçen mutlak bir rakamdır.
- **B) Plan payı** — aynı ay içindeki tüm session'lar arasında plan bedelinin
  dağıtılmış hali: `session_maliyeti / ay_toplamı × plan_tutarı`.
  Dağıtım anahtarı ham token değil **maliyettir**, çünkü output token input'un
  5 katı pahalıdır — tek savunulabilir anahtar budur.

Ay bitmemişse `(ay içi, geçici)` etiketi çıkar: ay ilerledikçe aynı session'ın
payı düşer. Beklenen davranış.

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
