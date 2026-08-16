---
name: cost
description: Use when the user wants to know how much time and money a Claude Code session cost — session duration, token usage, API-equivalent cost, and what share of their subscription plan it represents. Triggers on /cost, "bu session ne kadar tuttu", "maliyet", "süre", "ne kadar harcadım", "bu ay ne kadar harcadım", "plan payı".
---

# Session süre + maliyet raporu

Bu skill `scripts/claude_cost.py` betiğini çalıştırıp çıktısını kullanıcıya sunar.
**Betiğin işini sen yapmazsın** — rakamları sen hesaplamaz, tahmin etmez, yuvarlamazsın.

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

Kullanıcının isteğine göre argüman ekle:

| Kullanıcı ne isterse | Argüman |
|---|---|
| bu session (varsayılan) | *(yok)* |
| belirli bir session | `--session <id>` |
| bu ayın özeti | `--month` |
| belirli bir ay | `--month 2026-07` |
| "plan aslında $100" (geçici) | `--plan 100` |
| "plan tutarını kalıcı değiştir" | `--set-plan 100` |
| "ara eşiğini 10 dk yap" (geçici) | `--idle-gap 10` |
| "ara eşiğini kalıcı değiştir" | `--set-idle-gap 10` |
| para birimi değişikliği | `--currency EUR` (kalıcı için `--set-plan` ile birlikte) |
| makine okunur çıktı | `--json` |
| aracın kendini doğrulaması | `--selftest` |

## 3. Çıktıyı sun

- Betiğin çıktısını **olduğu gibi** ver. Yeniden biçimlendirme, özetleme, rakam
  uydurma veya "yaklaşık şu kadar" deme.
- Hata olursa hata metnini **aynen** ilet; sorunu kendin yorumlayıp gizleme.
- Çıktıda `WARN: bilinmeyen model` veya `EKSIK:` satırı varsa **öne çıkar** —
  o modelin maliyeti toplama dahil edilmemiştir, yani rakam eksiktir.
  Kullanıcıya `~/.claude/cost-config.json` içindeki `pricing_per_mtok` bölümüne
  o modeli eklemesi gerektiğini söyle.

## Bilinmesi gerekenler

- **Rakamlar API liste fiyatı üzerinden "API-karşılığı" maliyettir.** Abonelik
  (Pro/Max) planında gerçekte bu tutar tahsil edilmez; bu, session'ın ağırlığını
  ölçmek ve plan payını dağıtmak için kullanılan bir vekildir.
- **Plan payı** = session'ın API-karşılığı maliyeti ÷ ayın toplamı × plan tutarı.
  Dağıtım anahtarı ham token değil maliyettir (output token input'un 5 katı pahalı).
- **Ay bitmemişse** çıktıda `(ay içi, geçici)` etiketi görünür; ay ilerledikçe
  aynı session'ın payı düşer. Bu beklenen davranıştır, hata değil.
- **Molalar zaten düşülür.** Kullanıcı "aramı sayma", "limit yedim beklerken
  geçen süre sayılmasın" derse: `Aktif` satırı tam olarak budur, yeni bir şey
  gerekmez. Eşiği aşan her boşluk düşülür ve `haric tutulan aralar` listesinde
  tek tek gösterilir. Eşik uymuyorsa `--idle-gap <dk>` ile denet.
  Faturalanacak rakam `Duvar saati` değil **`Aktif`**'tir.
- Config dosyası: `~/.claude/cost-config.json` (ilk çalıştırmada otomatik oluşur,
  repoya girmez, her makinede yereldir).
