---
name: cost
description: Use when the user wants to know how much time and money a Claude Code session or month cost, wants to tag sessions as billable/personal, wants to set or check the plan-cost baseline, or wants to export session data from another machine — session duration, token usage, API-equivalent cost, and what share of their subscription plan it represents. Triggers on /cost, "bu session ne kadar tuttu", "maliyet", "süre", "ne kadar harcadım", "bu ay ne kadar harcadım", "plan payı", "bu müşteri işi", "etiketle", "taban", "dışa aktar".
---

# Session süre + maliyet raporu

Bu skill `scripts/claude_cost.py` betiğini çalıştırıp çıktısını kullanıcıya sunar.
**Betiğin işini sen yapmazsın** — rakamları sen hesaplamaz, tahmin etmez,
yuvarlamazsın. Kullanıcı hiçbir zaman Python komutu, dosya yolu veya
session UUID'i yazmak zorunda kalmamalı; bunları senin çözmen gerekir.

## 1. Yorumlayıcıyı bul

Sırayla dene, ilk çalışanı kullan:

1. `python3 --version`
2. `python --version`
3. `py -3 --version`

Bulunan sürüm **3.8 veya üstü** olmalı. Hiçbiri yoksa kuruluma yönlendir ve **dur**:

- Windows: `winget install Python.Python.3.12`
- Debian/Ubuntu: `sudo apt install python3`
- RHEL/Fedora: `sudo dnf install python3`
- macOS: `brew install python3`

## 2. Betiği çalıştır

```
<yorumlayıcı> "${CLAUDE_PLUGIN_ROOT}/scripts/claude_cost.py" [argümanlar]
```

Kullanıcının isteğine göre argüman(lar) seç. "Bu session" dendiğinde
kullanıcıya ID sorma — `$CLAUDE_CODE_SESSION_ID` ortam değişkenini kullan.

**Rapor / özet**

| Kullanıcı ne isterse | Argüman |
|---|---|
| bu session (varsayılan) | *(yok)* |
| belirli bir session | `--session <id>` |
| bu ayki/bu dönemki durum ne | `--month` |
| geçen ayki/dönemki maliyetim ne | `--month <YYYY-MM>` — geçen ayı bugünün tarihinden **sen** hesapla, kullanıcıya sorma |
| belirli bir ay | `--month 2026-07` (fatura dönemi takvim ayı değilse, o ayda **başlayan** döneme çözülür — bkz. "Bilinmesi gerekenler") |

**Plan / eşik (geçici = bu çalıştırma, kalıcı = config'e yazılır)**

| Kullanıcı ne isterse | Argüman |
|---|---|
| "plan aslında $100" (geçici) | `--plan 100` |
| "plan tutarını kalıcı değiştir" | `--set-plan 100` |
| "planın adı Max olsun" (kalıcı, `--set-plan` ile birlikte) | `--label Max` |
| "ara eşiğini 10 dk yap" (geçici) | `--idle-gap 10` |
| "ara eşiğini kalıcı değiştir" | `--set-idle-gap 10` |
| para birimi değişikliği | `--currency EUR` (kalıcı için `--set-plan` ile birlikte) |
| "takibi bu aydan başlat" | `--set-tracking-start <YYYY-MM>` |
| "fatura dönemim her ayın 8'inde başlıyor" | `--set-billing-day 8` (1-28 arası; 29/30/31 kabul edilmez çünkü her ayda o gün yok) |

**Taban (baseline) — bkz. "Bilinmesi gerekenler" için anlamı**

| Kullanıcı ne isterse | Argüman |
|---|---|
| "taban ne olsun" / "taban öner" | `--suggest-baseline` (salt okunur, hiçbir şey yazmaz) |
| "tabanı 1250 yap" | `--set-baseline 1250` |

**Etiketleme — bkz. §3 için UUID akışı ve onay akışı**

| Kullanıcı ne isterse | Argüman |
|---|---|
| "bu session'ı dahil et" / "bu müşteri işi" | `--tag $CLAUDE_CODE_SESSION_ID dahil` |
| "bunu sayma" / "kendi projem" | `--tag $CLAUDE_CODE_SESSION_ID haric` |
| "yanlış işaretledim, geri al" | `--untag <id>` |
| "etiketsizleri göster" | `--tag-list --untagged` |
| "hepsini göster" / "session'ları listele" | `--tag-list` |
| "Frames'in hepsi müşteri işi" (toplu, önce onaysız) | `--tag-project "*Frames*" dahil` |
| (kullanıcı onay verince) | aynı komut + `--yes` |

**Çoklu makine**

| Kullanıcı ne isterse | Argüman |
|---|---|
| "bu makinenin verisini dışa aktar" | `--export <dosya> --machine <ad>` |

**Çıktı biçimi / bakım**

| Kullanıcı ne isterse | Argüman |
|---|---|
| makine okunur çıktı | `--json` |
| aracın kendini doğrulaması | `--selftest` |
| sürüm bilgisi | `--version` |
| alternatif config/etiket dosyası (nadiren, hata ayıklama) | `--config <yol>` / `--tags <yol>` |

## 3. Etiketleme akışı — kullanıcıya UUID yazdırma

`--tag-list` çıktısı her session'ı **zaten numaralandırılmış** verir ve altına
UUID'sini yazar — ama **kullanıcı bu UUID'i asla kopyalamaz/yazmaz.**
Listeyi olduğu gibi kullanıcıya göster (numaralar dahil), kullanıcı
"1 ve 3 dahil, 2 hariç" gibi numarayla cevap verince, aynı listedeki
numara → UUID eşlemesini **sen** kullanarak gerekli `--tag` çağrılarını
kendin yaparsın. Numaralar yalnızca **o listeleme** içinde geçerlidir —
her seferinde `--tag-list` ile yeniden listele, önceki bir konuşmadan
kalan numaraya güvenme.

**Toplu etiketlemede önce onay al.** `--tag-project`, `--yes` olmadan
çalıştırıldığında hiçbir şey değiştirmez: kaç session'ın etkileneceğini
ve ilk birkaçını listeleyip **çıkış kodu 3** ile durur. Akış:

1. Komutu `--yes` **olmadan** çalıştır.
2. Çıktıdaki sayıyı ve örnek session'ları kullanıcıya göster: "N session
   'dahil' olarak işaretlenecek, örnekler: ...".
3. Kullanıcı onaylarsa aynı komutu `--yes` ekleyerek tekrar çalıştır.
4. Onaylamazsa hiçbir şey yapma — zaten hiçbir şey değişmedi.

Tek session etiketleme (`--tag <id> dahil|haric`) kullanıcının doğrudan
isteğinin kendisi olduğu için ayrıca onay gerektirmez.

## 4. Çıktıyı sun

- Betiğin çıktısını **olduğu gibi** ver. Yeniden biçimlendirme, özetleme, rakam
  uydurma veya "yaklaşık şu kadar" deme.
- Hata olursa hata metnini **aynen** ilet; sorunu kendin yorumlayıp gizleme.
- Çıktıda `WARN: bilinmeyen model` veya `EKSIK:` satırı varsa **öne çıkar** —
  o modelin maliyeti toplama dahil edilmemiştir, yani rakam eksiktir.
  Kullanıcıya `~/.claude/cost-config.json` içindeki `pricing_per_mtok` bölümüne
  o modeli eklemesi gerektiğini söyle.
- Aylık raporda `Etiketsiz: N session ... (hesaba KATILMADI)` satırı varsa
  bunu da öne çıkar — bu session'lar ne "dahil" ne "hariç" sayılmıştır,
  kullanıcıya etiketlemesini öner (`--tag-list --untagged` ile göster).
- "Taban set edilmemiş" hata mesajı çıkarsa rakam **hiç basılmaz** — bunu
  sadece iletmekle kalma, kullanıcıya `--suggest-baseline` çalıştırmayı
  teklif et (bkz. aşağıda).

## Bilinmesi gerekenler

- **Rakamlar API liste fiyatı üzerinden "API-karşılığı" maliyettir.** Abonelik
  (Pro/Max) planında gerçekte bu tutar tahsil edilmez; bu, session'ın ağırlığını
  dondurulmuş bir tabana oranlayarak ölçmek için kullanılan bir vekildir.
- **Plan maliyeti sabit orana dayanır ve session bitiminde sabitlenir:**
  `session_API_karşılığı ÷ TABAN × plan_tutarı`. Payda ay toplamı **değil**,
  config'e yazılmış **dondurulmuş taban**dır — bu yüzden bir session'ın rakamı
  bittiği anda kesinleşir, sonradan açılan başka oturumlardan etkilenip
  küçülmez. (Eski tasarımda payda ay-toplamıydı ve rakamlar ay ilerledikçe
  küçülürdü; bu artık geçerli değil.)
- **Taban kullanıcının seçimidir, sen seçmezsin.** Taban set edilmemişse
  rapor hiçbir rakam üretmez, sadece durumu bildirir. Önce `--suggest-baseline`
  çalıştır (hiçbir şey yazmaz, salt tavsiye), tavsiyeyi ve varsa etiketsiz
  session uyarısını kullanıcıya göster, kullanıcı bir sayıya karar verince
  `--set-baseline <tutar>` ile yaz. Sen kendiliğinden bir taban önerip yazma.
- **Üç etiket durumu vardır:** `dahil` (plan maliyetine sayılır), `haric`
  (sayılmaz) ve **`etiketsiz`** — üçüncü, ayrı bir durumdur. Etiketsiz
  session'lar ne toplama dahil edilir ne sessizce atlanır; ay raporunda ayrı
  bir satırda sayı ve tutarıyla gösterilir.
- **"Ay" her zaman takvim ayı değildir — `billing_cycle_day` set edilmişse
  bir fatura DÖNEMİdir.** `billing_cycle_day` 1 (varsayılan) değilse aylık
  raporun başlığı artık "Ağustos 2026" değil, `8 Ağustos 2026 - 8 Eylül 2026`
  gibi bir dönem aralığıdır — betiğin bastığı bu başlığı **aynen** ilet,
  kendin "ay" diye yeniden adlandırma. Kullanıcıya bundan bahsederken de
  "bu ay" yerine "bu dönem" / "bu fatura dönemi" de, özellikle ay ortasında
  büyük bir harcama varsa: o harcama, dönemin hangi tarafına düştüğüne göre
  önceki ya da sonraki döneme sayılır, takvim ayına değil.
- **Aylık toplam artık plan tutarına eşit değildir.** Bunun yerine bir
  kullanım oranı gösterilir: %130 "planı bu ay yoğun kullanıyorsun", %40
  "az kullanıyorsun" demektir. Bu bir hata değil, bilgidir.
- **Molalar zaten düşülür.** Kullanıcı "aramı sayma", "limit yedim beklerken
  geçen süre sayılmasın" derse: `Aktif` satırı tam olarak budur, yeni bir şey
  gerekmez. Eşiği aşan her boşluk düşülür ve `haric tutulan aralar` listesinde
  tek tek gösterilir. Eşik uymuyorsa `--idle-gap <dk>` ile denet.
  **Faturalanacak/raporlanacak rakam `Duvar saati` değil `Aktif`'tir.**
- **Çoklu makine.** Her makinede `--export <dosya> --machine <ad>` çalıştırılır
  (yalnızca özet rakamlar yazılır, **konuşma metni içermez**). Kullanıcı bu
  dosyayı ana makinede `~/.claude/cost-imports/` klasörüne kopyalar; sonraki
  ay/toplu raporlar bu makinelerin session'larını otomatik olarak birleştirip
  gösterir — ayrı bir birleştirme komutu yoktur. Session raporu (`--session`
  ya da argümansız) yerel tek bir transcript'i okur, `cost-imports/` dosyalarına
  hiç bakmaz — içe aktarılan session'lar yalnızca ay/toplu raporlarda görünür.
- Config dosyası: `~/.claude/cost-config.json`, etiket deposu:
  `~/.claude/cost-tags.json` (ilk çalıştırmada otomatik oluşur, repoya
  girmez, her makinede yereldir).
