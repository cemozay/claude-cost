# claude-cost 2.0.0 — Sabit oranlı plan maliyeti Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Session başına, session bitiminde sabitlenen ve bir daha değişmeyen bir plan maliyeti rakamı üretmek; yalnızca kullanıcının etiketlediği oturumları saymak; birden çok makinenin verisini birleştirmek.

**Architecture:** Tek dosyalık stdlib-only Python betiği (`scripts/claude_cost.py`) genişletilir. Üç yeni kalıcı durum eklenir: etiket deposu (`~/.claude/cost-tags.json`), takip başlangıç ayı ve dondurulmuş taban (ikisi de `cost-config.json` içinde). Mevcut `plan_share()` (ay-payı, geriye dönük değişen) kaldırılıp yerine dondurulmuş tabana bölen sabit formül gelir. Kullanıcı CLI'a hiç dokunmaz; SKILL.md doğal dili bayraklara çevirir.

**Tech Stack:** Python 3.8+, yalnızca stdlib (`json`, `argparse`, `datetime`, `pathlib`, `fnmatch`, `socket`, `statistics`). Test koşucusu yok — proje kendi `--selftest` moduyla test edilir.

## Global Constraints

- **Python 3.8 tabanı.** `match` deyimi, `list[str]` / `dict[str, X]` yerleşik jenerik notasyonu, `X | Y` tip sözdizimi **kullanılmaz**. Hedef Linux sunucusunda 3.8.10 var.
- **Yalnızca stdlib.** Üçüncü parti bağımlılık eklenmez.
- **Tüm dosya açmaları `encoding="utf-8"` ile.** Windows varsayılanı cp1254'tür ve Türkçe karakterlerde çöker.
- **Yollar `pathlib` ile.** Sabit `\` veya sürücü harfi literali yazılmaz.
- **Zaman damgaları `...Z` ile biter**; 3.8 `fromisoformat` bunu ayrıştıramaz, `Z` → `+00:00` çevrilir. Rapor yerel saatte gösterilir; export UTC ISO yazar.
- **Sessizce yanlış sayı basılmaz.** Eksik/bilinmeyen her durumda uyarı basılır ve rapora "eksik" notu düşer.
- **Selftest testtir.** Her davranış değişikliği `--selftest` içinde bir kontrolle korunur; kontrol önce yazılır ve başarısız olduğu görülür.
- **Çıktı dili Türkçe, ASCII gövdeli.** Kod içi dizeler mevcut dosyadaki gibi ASCII yazılır (`bosluk`, `haric`), Türkçe karakter kullanılmaz.
- Etiket sözlüğünde `True` = dahil, `False` = hariç, **anahtar yok** = etiketsiz.

---

### Task 1: Etiket deposu

**Files:**
- Modify: `scripts/claude_cost.py` (sabitler ~satır 28-31; yeni fonksiyonlar `save_config` sonrası ~satır 171; selftest `selftest()` içi)

**Interfaces:**
- Consumes: `Path`, `json`, `sys` (dosyada zaten import edilmiş)
- Produces: `TAGS_PATH`, `load_tags(path=None) -> dict`, `save_tags(store, path=None) -> Path`, `get_tag(store, session_id) -> True|False|None`, `set_tag(store, session_id, included) -> dict`, `remove_tag(store, session_id) -> dict`, `TAG_LABELS`, `parse_tag_word(word) -> True|False|None`

- [ ] **Step 1: Selftest kontrolünü yaz (başarısız olacak)**

`selftest()` içinde, `print("\n-- 10. Capraz platform` satırının **hemen öncesine** ekle:

```python
    print("\n-- 16. Etiket deposu --")
    import tempfile as _tf
    _td = _tf.mkdtemp(prefix="claude_cost_tags_")
    _tp = Path(_td) / "tags.json"
    try:
        st = load_tags(_tp)
        r16a = get_tag(st, "abc") is None
        set_tag(st, "abc", True)
        set_tag(st, "def", False)
        save_tags(st, _tp)
        st2 = load_tags(_tp)
        r16b = get_tag(st2, "abc") is True and get_tag(st2, "def") is False
        remove_tag(st2, "abc")
        save_tags(st2, _tp)
        r16c = get_tag(load_tags(_tp), "abc") is None
        results.append(_ok("16. Etiket yaz/oku/sil turu", r16a and r16b and r16c,
                           "yok->None, yaz->oku ayni, sil->None"))
    finally:
        try:
            _tp.unlink()
            os.rmdir(_td)
        except OSError:
            pass
```

- [ ] **Step 2: Testi çalıştır, başarısız olduğunu gör**

Run: `python scripts\claude_cost.py --selftest`
Expected: `NameError: name 'load_tags' is not defined`

- [ ] **Step 3: Sabiti ekle**

`scripts/claude_cost.py` satır 29 (`PROJECTS_DIR = ...`) altına:

```python
TAGS_PATH = Path.home() / ".claude" / "cost-tags.json"
```

- [ ] **Step 4: Etiket fonksiyonlarını ekle**

`save_config()` fonksiyonunun bitiminden sonra (`return path` satırının ardından, `# ---- 2. dosya bulma` yorum bloğundan önce) ekle:

```python
# ---------------------------------------------------------------- 1b. etiketler

# True = dahil, False = haric, anahtar YOK = etiketsiz.
TAG_LABELS = {True: "dahil", False: "haric", None: "etiketsiz"}


def parse_tag_word(word):
    """'dahil'/'haric' -> True/False. Taninmazsa None."""
    w = (word or "").strip().lower()
    if w in ("dahil", "include", "in"):
        return True
    if w in ("haric", "hariç", "exclude", "out"):
        return False
    return None


def load_tags(path=None):
    """Etiket deposunu okur. Yoksa/bozuksa bos depo doner (asla coker degil)."""
    path = Path(path) if path else TAGS_PATH
    if not path.exists():
        return {"version": 1, "tags": {}}
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        sys.stderr.write("UYARI: etiket dosyasi okunamadi ({}): {}\n".format(path, exc))
        return {"version": 1, "tags": {}}
    if not isinstance(data, dict) or not isinstance(data.get("tags"), dict):
        sys.stderr.write("UYARI: etiket dosyasi bozuk, bos kabul ediliyor: {}\n".format(path))
        return {"version": 1, "tags": {}}
    return data


def save_tags(store, path=None):
    path = Path(path) if path else TAGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=2, ensure_ascii=False, sort_keys=True)
    return path


def get_tag(store, session_id):
    """True=dahil, False=haric, None=etiketsiz."""
    return store.get("tags", {}).get(session_id)


def set_tag(store, session_id, included):
    store.setdefault("tags", {})[session_id] = bool(included)
    return store


def remove_tag(store, session_id):
    store.setdefault("tags", {}).pop(session_id, None)
    return store
```

- [ ] **Step 5: Testi çalıştır, geçtiğini gör**

Run: `python scripts\claude_cost.py --selftest`
Expected: `[PASS] 16. Etiket yaz/oku/sil turu` ve `SONUC: 16/16 kontrol gecti`

- [ ] **Step 6: Commit**

```bash
git add scripts/claude_cost.py
git commit -m "feat: etiket deposu (cost-tags.json) - yaz/oku/sil"
```

---

### Task 2: Takip başlangıç ayı

**Files:**
- Modify: `scripts/claude_cost.py` (`DEFAULT_CONFIG` ~satır 33; yeni `in_tracking_scope()`; `main()` CLI ~satır 863)

**Interfaces:**
- Consumes: `month_key(dt)` (satır 394), `load_config`/`save_config` (Task 1 öncesi mevcut)
- Produces: `in_tracking_scope(start_dt, config) -> bool`, config anahtarı `tracking_start_month` (string `"YYYY-MM"` veya `None`), CLI bayrağı `--set-tracking-start`

- [ ] **Step 1: Selftest kontrolünü yaz**

Task 1'de eklediğin 16 numaralı bloğun hemen altına:

```python
    print("\n-- 22. Takip baslangici --")
    _c22 = json.loads(json.dumps(config))
    _c22["tracking_start_month"] = "2026-08"
    _t_in = parse_ts("2026-08-05T10:00:00.000Z")
    _t_out = parse_ts("2026-07-31T23:59:59.000Z")
    _c22none = json.loads(json.dumps(config))
    _c22none["tracking_start_month"] = None
    results.append(_ok(
        "22. Takip baslangicindan onceki session kapsam disi",
        in_tracking_scope(_t_in, _c22) is True
        and in_tracking_scope(_t_out, _c22) is False
        and in_tracking_scope(_t_out, _c22none) is True,
        "2026-08 esigi: Agu ici -> True, Tem -> False, esik yokken -> True"))
```

- [ ] **Step 2: Testi çalıştır, başarısız olduğunu gör**

Run: `python scripts\claude_cost.py --selftest`
Expected: `NameError: name 'in_tracking_scope' is not defined`

- [ ] **Step 3: Config varsayılanına anahtar ekle**

`DEFAULT_CONFIG` içinde `"idle_gap_seconds": 300,` satırının altına:

```python
    # Bu aydan ONCE baslayan session'lar hic gorunmez: rapora girmez,
    # --tag-list'te listelenmez, "etiketsiz" uyarisi uretmez.
    # None = tum gecmis kapsamda (geriye uyumluluk).
    "tracking_start_month": None,
```

- [ ] **Step 4: `in_tracking_scope()` ekle**

`month_key()` fonksiyonunun (satır 394) hemen altına:

```python
def in_tracking_scope(start_dt, config):
    """Session takip kapsaminda mi? month_key 'YYYY-MM' dondugu icin
    dize karsilastirmasi kronolojik siralamayla ayni sonucu verir."""
    start_month = config.get("tracking_start_month")
    if not start_month:
        return True
    if start_dt is None:
        return False
    return month_key(start_dt) >= start_month
```

- [ ] **Step 5: CLI bayrağını ekle**

`main()` içinde `--set-idle-gap` argümanının altına:

```python
    ap.add_argument("--set-tracking-start", metavar="YYYY-MM",
                    help="takip baslangic ayini config'e kalici yaz")
```

Ardından `if args.set_plan is not None or args.set_idle_gap is not None:` koşulunu şu şekilde genişlet ve gövdesine ekle:

```python
    if (args.set_plan is not None or args.set_idle_gap is not None
            or args.set_tracking_start is not None):
```

Gövdede `if args.set_idle_gap is not None:` bloğunun ardına:

```python
        if args.set_tracking_start is not None:
            txt = args.set_tracking_start.strip()
            try:
                datetime.datetime.strptime(txt, "%Y-%m")
            except ValueError:
                sys.stderr.write("HATA: --set-tracking-start YYYY-MM biciminde "
                                 "olmali (ornek 2026-08).\n")
                return 2
            # Kanonik bicime cevrilerek saklanir. strptime "2026-1"i de kabul
            # eder; ham saklanirsa in_tracking_scope'un dize karsilastirmasi
            # bozulur ("2026-08" >= "2026-1" False doner) ve oturumlar sessizce
            # kaybolur. Global Constraint: sessizce yanlis sayi basilmaz.
            config["tracking_start_month"] = datetime.datetime.strptime(
                txt, "%Y-%m").strftime("%Y-%m")
```

Ve aynı bloğun sonundaki özet çıktısına (`print("  ara esigi: ...")` altına):

```python
        print("  takip baslangici: {}".format(
            config.get("tracking_start_month") or "(tum gecmis)"))
```

- [ ] **Step 6: Testi çalıştır, geçtiğini gör**

Run: `python scripts\claude_cost.py --selftest`
Expected: `[PASS] 22. Takip baslangicindan onceki session kapsam disi`, `SONUC: 17/17`

- [ ] **Step 7: CLI'ı elle doğrula**

Run: `python scripts\claude_cost.py --set-tracking-start 2026-13`
Expected: `HATA: --set-tracking-start YYYY-MM biciminde olmali (ornek 2026-08).`, çıkış kodu 2

Run: `python scripts\claude_cost.py --set-tracking-start 2026-08`
Expected: `takip baslangici: 2026-08`

- [ ] **Step 8: Commit**

```bash
git add scripts/claude_cost.py
git commit -m "feat: takip baslangic ayi (tracking_start_month)"
```

---

### Task 3: Session toplama + `--tag-list` / `--tag` / `--untag`

**Files:**
- Modify: `scripts/claude_cost.py` (`month_totals` öncesi yeni `collect_sessions()`; `main()` CLI)

**Interfaces:**
- Consumes: `parse_session`, `compute_duration`, `price_requests`, `in_tracking_scope`, `load_tags`/`get_tag`/`set_tag`/`remove_tag`, `all_session_files`
- Produces: `collect_sessions(config, tags=None, quiet=True) -> list` — her eleman:
  `{"session_id", "title", "cwd", "gitBranch", "slug", "path", "machine",
    "start", "end", "active_seconds", "wall_seconds", "cost", "tokens",
    "per_model", "request_count", "unknown_models", "tag"}`
  (`tag` = `True`/`False`/`None`). Liste `start` artan sıralı.

- [ ] **Step 1: Selftest kontrolünü yaz**

16 ve 22 numaralı blokların altına:

```python
    print("\n-- 16b. collect_sessions --")
    _sessions = collect_sessions(config)
    _has_fields = all(
        all(k in s for k in ("session_id", "start", "cost", "tag", "active_seconds"))
        for s in _sessions)
    _sorted = all(_sessions[i]["start"] <= _sessions[i + 1]["start"]
                  for i in range(len(_sessions) - 1))
    results.append(_ok("16b. collect_sessions alanlari tam ve start'a gore sirali",
                       _has_fields and _sorted and len(_sessions) > 0,
                       "{} session".format(len(_sessions))))
```

- [ ] **Step 2: Testi çalıştır, başarısız olduğunu gör**

Run: `python scripts\claude_cost.py --selftest`
Expected: `NameError: name 'collect_sessions' is not defined`

- [ ] **Step 3: `collect_sessions()` ekle**

`month_key()` / `in_tracking_scope()` bloğunun altına, `month_totals()` üstüne:

```python
def collect_sessions(config, tags=None, quiet=True):
    """Kapsamdaki tum session'larin ozetini toplar.

    Takip baslangicindan onceki session'lar tamamen elenir.
    """
    if tags is None:
        tags = load_tags()
    out = []
    stderr = sys.stderr
    if quiet:
        sys.stderr = open(os.devnull, "w")
    try:
        for path in all_session_files():
            parsed = parse_session(path)
            if not parsed["timestamps"] or not parsed["requests"]:
                continue
            start = parsed["timestamps"][0]
            if not in_tracking_scope(start, config):
                continue
            priced = price_requests(parsed["requests"], config)
            dur = compute_duration(parsed["timestamps"],
                                   float(config.get("idle_gap_seconds", 300)))
            sid = parsed["meta"].get("sessionId") or Path(path).stem
            out.append({
                "session_id": sid,
                "title": parsed["meta"].get("title"),
                "cwd": parsed["meta"].get("cwd"),
                "gitBranch": parsed["meta"].get("gitBranch"),
                "slug": parsed["meta"].get("slug"),
                "path": str(path),
                "machine": "(yerel)",
                "start": start,
                "end": parsed["timestamps"][-1],
                "active_seconds": dur["active"],
                "wall_seconds": dur["wall"],
                "cost": priced["total_cost"],
                "tokens": priced["totals"],
                "per_model": priced["per_model"],
                "request_count": priced["request_count"],
                "unknown_models": sorted(priced["unknown_models"]),
                "tag": get_tag(tags, sid),
            })
    finally:
        if quiet:
            sys.stderr.close()
            sys.stderr = stderr
    out.sort(key=lambda s: s["start"])
    return out
```

- [ ] **Step 4: Testi çalıştır, geçtiğini gör**

Run: `python scripts\claude_cost.py --selftest`
Expected: `[PASS] 16b. collect_sessions alanlari tam ve start'a gore sirali`, `SONUC: 18/18`

- [ ] **Step 5: `--tag-list` çıktısını üreten fonksiyonu ekle**

`render_json()` fonksiyonundan sonra ekle:

```python
def render_tag_list(sessions, only_untagged=False):
    rows = [s for s in sessions
            if not only_untagged or s["tag"] is None]
    if not rows:
        return "Listelenecek session yok." if not only_untagged \
            else "Etiketsiz session yok - hepsi isaretlenmis."
    L = []
    baslik = "Etiketsiz oturumlar" if only_untagged else "Oturumlar"
    L.append("{} ({} adet):".format(baslik, len(rows)))
    L.append("")
    for i, s in enumerate(rows, 1):
        st = to_local(s["start"])
        proje = Path(s["cwd"]).name if s["cwd"] else (s["slug"] or "-")
        L.append("{:>3}. {}  {:<34} {:>10} {:>10}  {:<16} [{}]".format(
            i,
            "{:>2} {} {}".format(st.day, TR_MONTHS[st.month][:3],
                                 st.strftime("%H:%M")),
            (s["title"] or s["session_id"])[:34],
            fmt_dur(s["active_seconds"]),
            fmt_money(s["cost"]),
            proje[:16],
            TAG_LABELS[s["tag"]]))
        L.append("     {}".format(s["session_id"]))
    return "\n".join(L)
```

- [ ] **Step 6: CLI bayraklarını ekle**

`main()` içinde `--set-tracking-start` altına:

```python
    ap.add_argument("--tag", nargs=2, metavar=("SESSION-ID", "dahil|haric"),
                    help="tek session'i etiketle")
    ap.add_argument("--untag", metavar="SESSION-ID",
                    help="etiketi kaldir (etiketsize dondur)")
    ap.add_argument("--tag-list", action="store_true",
                    help="session'lari etiketleriyle listele")
    ap.add_argument("--untagged", action="store_true",
                    help="--tag-list ile: yalnizca etiketsizleri goster")
```

- [ ] **Step 7: CLI mantığını ekle**

`main()` içinde, `if args.selftest:` bloğunun **hemen öncesine**:

```python
    if args.tag:
        sid, word = args.tag
        included = parse_tag_word(word)
        if included is None:
            sys.stderr.write("HATA: etiket 'dahil' veya 'haric' olmali "
                             "('{}' verildi).\n".format(word))
            return 2
        known = set(s["session_id"] for s in collect_sessions(config))
        if sid not in known:
            sys.stderr.write("HATA: '{}' kapsamda bir session degil. "
                             "--tag-list ile bakin.\n".format(sid))
            return 2
        store = load_tags()
        set_tag(store, sid, included)
        written = save_tags(store)
        print("Etiketlendi: {} -> {}".format(sid, TAG_LABELS[included]))
        print("  {}".format(written))
        return 0

    if args.untag:
        store = load_tags()
        if get_tag(store, args.untag) is None:
            print("Zaten etiketsiz: {}".format(args.untag))
            return 0
        remove_tag(store, args.untag)
        save_tags(store)
        print("Etiket kaldirildi: {} -> etiketsiz".format(args.untag))
        return 0

    if args.tag_list:
        print(render_tag_list(collect_sessions(config),
                              only_untagged=args.untagged))
        return 0
```

- [ ] **Step 8: Elle doğrula**

Run: `python scripts\claude_cost.py --tag-list --untagged`
Expected: numaralı liste, her satırda `[etiketsiz]`

Run: `python scripts\claude_cost.py --tag bilinmeyen-id dahil`
Expected: `HATA: 'bilinmeyen-id' kapsamda bir session degil.`, çıkış kodu 2

Run: `python scripts\claude_cost.py --tag <listeden-bir-uuid> dahil` sonra `--tag-list`
Expected: o satır artık `[dahil]`

- [ ] **Step 9: Commit**

```bash
git add scripts/claude_cost.py
git commit -m "feat: collect_sessions + --tag / --untag / --tag-list"
```

---

### Task 4: `--tag-project` toplu tohumlama

**Files:**
- Modify: `scripts/claude_cost.py` (`main()` CLI)

**Interfaces:**
- Consumes: `collect_sessions`, `load_tags`/`set_tag`/`save_tags`, `parse_tag_word`, `TAG_LABELS`
- Produces: CLI bayrakları `--tag-project <desen> <dahil|haric>` ve `--yes`

- [ ] **Step 1: `fnmatch` importunu ekle**

Dosya başındaki import bloğuna (`import datetime` altına):

```python
import fnmatch
```

- [ ] **Step 2: CLI bayraklarını ekle**

`main()` içinde `--untagged` altına:

```python
    ap.add_argument("--tag-project", nargs=2, metavar=("DESEN", "dahil|haric"),
                    help="cwd desenine uyan session'lari toplu etiketle")
    ap.add_argument("--yes", action="store_true",
                    help="toplu islemde onay sorma")
```

- [ ] **Step 3: Mantığı ekle**

`if args.tag_list:` bloğunun **hemen öncesine**:

```python
    if args.tag_project:
        desen, word = args.tag_project
        included = parse_tag_word(word)
        if included is None:
            sys.stderr.write("HATA: etiket 'dahil' veya 'haric' olmali "
                             "('{}' verildi).\n".format(word))
            return 2
        # fnmatch buyuk/kucuk harf duyarsiz karsilastirilir: Windows yollari icin sart.
        d = desen.lower()
        hedef = [s for s in collect_sessions(config)
                 if s["cwd"] and fnmatch.fnmatch(s["cwd"].lower(), d)]
        if not hedef:
            print("Desene uyan session yok: {}".format(desen))
            return 0
        print("{} session '{}' olarak etiketlenecek:".format(
            len(hedef), TAG_LABELS[included]))
        for s in hedef[:5]:
            print("  {}  {}".format(to_local(s["start"]).strftime("%d %b %H:%M"),
                                    (s["title"] or s["session_id"])[:44]))
        if len(hedef) > 5:
            print("  ... {} tane daha".format(len(hedef) - 5))
        if not args.yes:
            sys.stderr.write("Onay gerekli: ayni komutu --yes ile calistirin.\n")
            return 3
        store = load_tags()
        for s in hedef:
            set_tag(store, s["session_id"], included)
        save_tags(store)
        print("{} session etiketlendi.".format(len(hedef)))
        return 0
```

- [ ] **Step 4: Elle doğrula — onaysız durmalı**

Run: `python scripts\claude_cost.py --tag-project "*Frames*" dahil`
Expected: kaç session etkileneceğini listeler, `Onay gerekli: ayni komutu --yes ile calistirin.`, çıkış kodu 3, **etiket dosyası değişmemiş olmalı**

- [ ] **Step 5: Elle doğrula — onaylı yazmalı**

Run: `python scripts\claude_cost.py --tag-project "*Frames*" dahil --yes`
Expected: `N session etiketlendi.`

Run: `python scripts\claude_cost.py --tag-list --untagged`
Expected: Frames oturumları artık listede yok

- [ ] **Step 6: Commit**

```bash
git add scripts/claude_cost.py
git commit -m "feat: --tag-project toplu tohumlama (onayli)"
```

---

### Task 5: Taban — `--set-baseline` / `--suggest-baseline`

**Files:**
- Modify: `scripts/claude_cost.py` (`DEFAULT_CONFIG`; yeni `suggest_baseline_text()`; `main()` CLI)

**Interfaces:**
- Consumes: `collect_sessions`, `month_key`, `is_current_month`, `fmt_money`
- Produces: config anahtarları `baseline_monthly_api_cost` (float veya `None`), `baseline_source` (dict veya `None`); `suggest_baseline_text(config) -> str`; CLI `--set-baseline`, `--suggest-baseline`

- [ ] **Step 1: Selftest kontrolünü yaz (21 — taban yokluğu)**

Selftest içine, 22 numaralı bloğun altına:

```python
    print("\n-- 21. Taban yoklugu --")
    _c21 = json.loads(json.dumps(config))
    _c21["baseline_monthly_api_cost"] = None
    try:
        fixed_plan_cost(100.0, _c21)
        r21 = False
        det21 = "hata firlatmadi"
    except BaselineNotSetError:
        r21 = True
        det21 = "BaselineNotSetError firlatildi"
    results.append(_ok("21. Taban yokken sayi uretilmiyor", r21, det21))
```

- [ ] **Step 2: Testi çalıştır, başarısız olduğunu gör**

Run: `python scripts\claude_cost.py --selftest`
Expected: `NameError: name 'fixed_plan_cost' is not defined`

- [ ] **Step 3: Config varsayılanlarına anahtar ekle**

`DEFAULT_CONFIG` içinde `"tracking_start_month": None,` altına:

```python
    # Dondurulmus taban. ASLA kendiliginden degismez; yalnizca --set-baseline yazar.
    # None ise rapor sayi uretmez, hata verir.
    "baseline_monthly_api_cost": None,
    "baseline_source": None,
```

- [ ] **Step 4: İstisnayı ve formülü ekle**

> **`plan_share()`'i şimdi SİLME.** `render_text` ve `build_session_report`
> hâlâ onu çağırıyor; silersen bu commit `--month`'u ve varsayılan raporu
> çökertir. Son çağıran Task 7'de kalkıyor, silme oraya bırakıldı.

`plan_share()` fonksiyonunun (satır 439-449) **hemen altına** ekle:

```python
class BaselineNotSetError(Exception):
    """Taban set edilmemis. Rapor sayi uydurmaz, durur."""


def fixed_plan_cost(session_cost, config):
    """Sabit oranli plan maliyeti.

    session_API_karsiligi / TABAN x plan_tutari

    Payda ay toplami DEGIL dondurulmus tabandir; bu yuzden rakam session
    bitiminde sabitlenir ve sonradan acilan oturumlardan etkilenmez.
    """
    baseline = config.get("baseline_monthly_api_cost")
    if not baseline or float(baseline) <= 0:
        raise BaselineNotSetError(
            "Taban set edilmemis. Once '--suggest-baseline' ile bakin, "
            "sonra '--set-baseline <tutar>' ile yazin.")
    plan_amount = float(config.get("plan", {}).get("amount", 0.0))
    ratio = float(session_cost) / float(baseline)
    return {"ratio": ratio, "amount": ratio * plan_amount,
            "baseline": float(baseline)}
```

- [ ] **Step 5: Testi çalıştır, geçtiğini gör**

Run: `python scripts\claude_cost.py --selftest`
Expected: `[PASS] 21. Taban yokken sayi uretilmiyor`, `SONUC: 19/19`

Run: `python scripts\claude_cost.py --month`
Expected: hâlâ çalışıyor (eski ay-payı çıktısı) — bu commit hiçbir şeyi kırmaz.

- [ ] **Step 6: `suggest_baseline_text()` ekle**

`fixed_plan_cost()` altına:

```python
def suggest_baseline_text(config):
    """Yalnizca TAVSIYE. Hicbir sey yazmaz."""
    sessions = collect_sessions(config)
    now = datetime.datetime.now()
    mk = now.strftime("%Y-%m")
    dahil = [s for s in sessions if s["tag"] is True and month_key(s["start"]) == mk]
    etiketsiz = [s for s in sessions if s["tag"] is None and month_key(s["start"]) == mk]
    toplam = sum(s["cost"] for s in dahil)

    import calendar
    gun_sayisi = calendar.monthrange(now.year, now.month)[1]
    gecen = now.day
    izdusum = toplam * gun_sayisi / gecen if gecen > 0 else 0.0

    L = []
    L.append("Takip baslangici: {}".format(
        config.get("tracking_start_month") or "(tum gecmis)"))
    L.append("")
    L.append("  Dahil edilen ({}, su ana kadar) : {} session   {}".format(
        mk, len(dahil), fmt_money(toplam)))
    L.append("  Ayin gecen kismi                : {}/{} gun".format(gecen, gun_sayisi))
    L.append("  Dogrusal izdusum (tam ay)       : ~{}   [TAHMIN]".format(
        fmt_money(izdusum)))
    if etiketsiz:
        L.append("")
        L.append("  Etiketsiz: {} session  {}   <-- once bunlari etiketle, "
                 "sayi degisir".format(len(etiketsiz),
                                       fmt_money(sum(s["cost"] for s in etiketsiz))))
    L.append("")
    L.append("Izdusum duzenli kullanim varsayar; kullanimin dalgaliysa yanilir.")
    L.append("Tabani sen seciyorsun. Ornek:")
    L.append("  claude_cost.py --set-baseline {:.0f}".format(max(izdusum, 1.0)))
    return "\n".join(L)
```

- [ ] **Step 7: CLI bayraklarını ekle ve bağla**

`main()` içinde `--yes` altına:

```python
    ap.add_argument("--set-baseline", type=float, metavar="TUTAR",
                    help="tabani config'e kalici yaz")
    ap.add_argument("--suggest-baseline", action="store_true",
                    help="taban icin tavsiye goster (hicbir sey yazmaz)")
```

`if args.tag_project:` bloğunun öncesine:

```python
    if args.suggest_baseline:
        print(suggest_baseline_text(config))
        return 0

    if args.set_baseline is not None:
        if args.set_baseline <= 0:
            sys.stderr.write("HATA: --set-baseline sifirdan buyuk olmali.\n")
            return 2
        config["baseline_monthly_api_cost"] = float(args.set_baseline)
        config["baseline_source"] = {
            "set_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "method": "manual",
        }
        written = save_config(config, config_path)
        print("Taban yazildi: {}/ay".format(fmt_money(args.set_baseline)))
        print("  {}".format(written))
        return 0
```

- [ ] **Step 8: Elle doğrula**

Run: `python scripts\claude_cost.py --suggest-baseline`
Expected: dahil/etiketsiz dökümü, `[TAHMIN]` etiketli izdüşüm, örnek komut. Config dosyası **değişmemiş** olmalı.

Run: `python scripts\claude_cost.py --set-baseline 0`
Expected: `HATA: --set-baseline sifirdan buyuk olmali.`, çıkış kodu 2

Run: `python scripts\claude_cost.py --set-baseline 1250`
Expected: `Taban yazildi: $1,250.00/ay`

- [ ] **Step 9: Commit**

```bash
git add scripts/claude_cost.py
git commit -m "feat: dondurulmus taban - --set-baseline / --suggest-baseline"
```

---

### Task 6: Sabit oran formülü + session raporunun B bölümü

**Files:**
- Modify: `scripts/claude_cost.py` (`render_text` ~satır 457-562; `build_session_report` ~satır 576-617)

**Interfaces:**
- Consumes: `fixed_plan_cost`, `BaselineNotSetError`, `get_tag`, `TAG_LABELS`
- Produces: `build_session_report` raporuna `tag` ve `fixed_cost` alanları; `render_text` içinde yeni `B) Plan maliyeti (sabit oran)` bölümü

- [ ] **Step 1: Selftest kontrolünü yaz (19 — sabit oran değişmezliği)**

Selftest içinde 21 numaralı bloğun altına:

```python
    print("\n-- 19. Sabit oran degismezligi --")
    _c19 = json.loads(json.dumps(config))
    _c19["baseline_monthly_api_cost"] = 1000.0
    _c19["plan"] = {"amount": 20.0, "currency": "USD", "label": "Pro"}
    _once = fixed_plan_cost(50.0, _c19)["amount"]
    # Ay toplaminin degismesi rakami ETKILEMEMELI: payda taban, ay toplami degil.
    _sonra = fixed_plan_cost(50.0, _c19)["amount"]
    _cift = fixed_plan_cost(100.0, _c19)["amount"]
    results.append(_ok(
        "19. Ay toplami degisse de session rakami sabit",
        abs(_once - _sonra) < 1e-12 and abs(_once - 1.0) < 1e-9
        and abs(_cift - 2.0) < 1e-9,
        "50/1000x20 = {:.4f} (iki cagride ayni), 100 -> {:.4f}".format(_once, _cift)))
```

- [ ] **Step 2: Testi çalıştır, geçtiğini gör**

Run: `python scripts\claude_cost.py --selftest`
Expected: `[PASS] 19. Ay toplami degisse de session rakami sabit`

- [ ] **Step 3: `build_session_report()` içindeki ay-payı bloğunu değiştir**

`build_session_report` sonundaki şu bloğu:

```python
    if with_month and duration["start"] is not None:
        mk = month_key(duration["start"])
        totals = month_totals(mk, config)
        report["month_context"] = {
            "month": mk,
            "session_count": totals["session_count"],
            "total_cost": totals["total_cost"],
            "share": plan_share(priced["total_cost"], totals["total_cost"],
                                float(plan.get("amount", 0.0))),
        }
    return report
```

şununla değiştir:

```python
    tags = load_tags()
    report["tag"] = get_tag(tags, report["session"]["session_id"])
    report["in_scope"] = in_tracking_scope(duration["start"], config)

    report["fixed_cost"] = None
    report["baseline_error"] = None
    if report["tag"] is True:
        try:
            report["fixed_cost"] = fixed_plan_cost(priced["total_cost"], config)
        except BaselineNotSetError as exc:
            report["baseline_error"] = str(exc)
    return report
```

Ayrıca fonksiyon imzasındaki `with_month=True` parametresi artık kullanılmıyor —
imzayı `def build_session_report(path, config, config_path):` olarak sadeleştir
ve `main()` içindeki çağrıyı da buna göre güncelle.

- [ ] **Step 4: `render_text()` içindeki B bölümünü değiştir**

`render_text` içinde `A) API-karsiligi maliyet` bloğundaki şu satırları **sil**:

```python
        plan = report["plan"]
        if plan["amount"] > 0:
            pct = report["total_cost"] / plan["amount"] * 100.0
            L.append("  {:<20} {:>10}  -> {} plan {} tutarinin %{:.1f}'i".format(
                "", "", plan.get("label", ""), fmt_money(plan["amount"], cur), pct))
        L.append("")
```

yerine sadece:

```python
        L.append("")
```

Ardından `m = report.get("month_context")` ile başlayan bloğun **tamamını** şununla değiştir:

```python
        plan = report["plan"]
        L.append("B) Plan maliyeti (sabit oran)")
        if report.get("tag") is False:
            L.append("  Bu session HARIC tutulmus - plan maliyetine katilmiyor.")
        elif report.get("tag") is None:
            L.append("  Bu session ETIKETSIZ - plan maliyeti hesaplanmadi.")
            L.append("  Dahil etmek icin: --tag {} dahil".format(
                report["session"]["session_id"]))
        elif report.get("baseline_error"):
            L.append("  {}".format(report["baseline_error"]))
        else:
            fc = report["fixed_cost"]
            src = report.get("baseline_source") or {}
            not_ = src.get("set_at", "")[:10]
            L.append("  Taban          : {}/ay{}".format(
                fmt_money(fc["baseline"], cur),
                "   ({} tarihinde elle konuldu)".format(not_) if not_ else ""))
            L.append("  Bu session     : {} / {} x {}  =  {}   [SABIT]".format(
                fmt_money(report["total_cost"], cur),
                fmt_money(fc["baseline"], cur),
                fmt_money(plan["amount"], cur),
                fmt_money(fc["amount"], cur)))
```

Ayrıca `Etiket:` satırını başlığa ekle — `L.append("Proje:     {}".format(proje))` satırının altına:

```python
        L.append("Etiket:    {}".format(TAG_LABELS[report.get("tag")]))
```

- [ ] **Step 5: `baseline_source`'u rapora taşı**

`build_session_report` içindeki `report = {` sözlüğüne, `"plan": plan,` satırının altına ekle:

```python
        "baseline_source": config.get("baseline_source"),
```

- [ ] **Step 6: Elle doğrula — üç etiket durumu**

Run: `python scripts\claude_cost.py --tag <bu-session-id> haric` sonra `python scripts\claude_cost.py`
Expected: `Etiket:    haric` ve `Bu session HARIC tutulmus`

Run: `python scripts\claude_cost.py --untag <bu-session-id>` sonra rapor
Expected: `Etiket:    etiketsiz` ve `--tag ... dahil` önerisi

Run: `python scripts\claude_cost.py --tag <bu-session-id> dahil` sonra rapor
Expected: `[SABIT]` satırı, `-> Pro plan ... %` satırı **görünmemeli**

- [ ] **Step 7: Selftest'i çalıştır**

Run: `python scripts\claude_cost.py --selftest`
Expected: tüm kontroller PASS

- [ ] **Step 8: Commit**

```bash
git add scripts/claude_cost.py
git commit -m "feat: sabit oranli plan maliyeti, yaniltici plan yuzdesi kaldirildi"
```

---

### Task 7: Aylık raporun etiket kırılımı

**Files:**
- Modify: `scripts/claude_cost.py` (`month_totals` ~satır 398-437; `build_month_report`; `render_text` aylık dalı)

**Interfaces:**
- Consumes: `collect_sessions`, `fixed_plan_cost`, `BaselineNotSetError`
- Produces: `month_totals(month, config) -> {"month", "dahil", "haric", "etiketsiz", "dahil_cost", "haric_cost", "etiketsiz_cost", "utilization", "plan_cost", "baseline"}`

- [ ] **Step 1: Selftest kontrolünü yaz (20 — etiketsiz izolasyonu)**

Selftest içine 19 numaralı bloğun altına:

```python
    print("\n-- 20. Etiketsiz izolasyonu --")
    _mk20 = datetime.datetime.now().strftime("%Y-%m")
    _mt20 = month_totals(_mk20, config)
    _toplam_ayri = (abs((_mt20["dahil_cost"] + _mt20["haric_cost"]
                         + _mt20["etiketsiz_cost"])
                        - _mt20["tum_cost"]) < 0.01)
    _etiketsiz_haric = all(s["tag"] is True for s in _mt20["dahil"])
    results.append(_ok(
        "20. Etiketsizler dahil toplamina girmiyor, ayri sayiliyor",
        _toplam_ayri and _etiketsiz_haric,
        "dahil {} / haric {} / etiketsiz {}".format(
            len(_mt20["dahil"]), len(_mt20["haric"]), len(_mt20["etiketsiz"]))))
```

- [ ] **Step 2: Testi çalıştır, başarısız olduğunu gör**

Run: `python scripts\claude_cost.py --selftest`
Expected: `KeyError: 'dahil_cost'`

- [ ] **Step 3: `month_totals()` fonksiyonunu değiştir**

Mevcut `month_totals()` gövdesini **tamamen** şununla değiştir:

```python
def month_totals(month, config, quiet=True):
    """Ay ozetini etiket kirilimiyla dondurur.

    Session'lar BASLANGIC ayina gore gruplanir. Tum projeler ve (varsa)
    import edilmis makineler dahildir.
    """
    sessions = [s for s in collect_sessions(config, quiet=quiet)
                if month_key(s["start"]) == month]
    dahil = [s for s in sessions if s["tag"] is True]
    haric = [s for s in sessions if s["tag"] is False]
    etiketsiz = [s for s in sessions if s["tag"] is None]

    dahil_cost = sum(s["cost"] for s in dahil)
    out = {
        "month": month,
        "dahil": dahil, "haric": haric, "etiketsiz": etiketsiz,
        "dahil_cost": dahil_cost,
        "haric_cost": sum(s["cost"] for s in haric),
        "etiketsiz_cost": sum(s["cost"] for s in etiketsiz),
        "tum_cost": sum(s["cost"] for s in sessions),
        "utilization": None, "plan_cost": None, "baseline": None,
        "baseline_error": None,
    }
    try:
        fc = fixed_plan_cost(dahil_cost, config)
        out["utilization"] = fc["ratio"]
        out["plan_cost"] = fc["amount"]
        out["baseline"] = fc["baseline"]
    except BaselineNotSetError as exc:
        out["baseline_error"] = str(exc)
    return out
```

- [ ] **Step 4: `build_month_report()` fonksiyonunu değiştir**

Mevcut gövdeyi şununla değiştir:

```python
def build_month_report(month, config, config_path):
    totals = month_totals(month, config)
    unknown = set()
    for grup in (totals["dahil"], totals["haric"], totals["etiketsiz"]):
        for s in grup:
            unknown.update(s.get("unknown_models") or [])
    return {
        "mode": "month",
        "version": __version__,
        "config_path": str(config_path),
        "plan": config.get("plan", DEFAULT_CONFIG["plan"]),
        "month": totals,
        "unknown_models": sorted(unknown),
    }
```

- [ ] **Step 5: `render_text()` aylık dalını değiştir**

`render_text` içindeki `else:` dalının (aylık rapor) tamamını şununla değiştir:

```python
    else:
        m = report["month"]
        mk = m["month"]
        y, mo = mk.split("-")
        etiket = "  (ay ici, gecici)" if is_current_month(mk) else ""
        plan = report["plan"]
        L.append("{} {}{}".format(TR_MONTHS[int(mo)], y, etiket))
        L.append("")
        L.append("  Dahil     : {:>3} session   {:>10}".format(
            len(m["dahil"]), fmt_money(m["dahil_cost"], cur)))
        if m["baseline_error"]:
            L.append("  {}".format(m["baseline_error"]))
        else:
            L.append("  Taban     : {}/ay  ->  kullanim %{:.1f}  ->  "
                     "plan maliyeti {}".format(
                         fmt_money(m["baseline"], cur),
                         m["utilization"] * 100.0,
                         fmt_money(m["plan_cost"], cur)))
        if m["etiketsiz"]:
            L.append("  Etiketsiz : {:>3} session   {:>10}   (hesaba KATILMADI)".format(
                len(m["etiketsiz"]), fmt_money(m["etiketsiz_cost"], cur)))
        if m["haric"]:
            L.append("  Haric     : {:>3} session   {:>10}".format(
                len(m["haric"]), fmt_money(m["haric_cost"], cur)))
```

- [ ] **Step 6: Artık çağrılmayan `plan_share()`'i sil**

Son çağıran da kalktığına göre `plan_share()` fonksiyonunu (satır 439-449)
**tamamen sil.** Silmeden önce başka çağıran kalmadığını doğrula:

Run: `python -c "import io;d=open(r'scripts/claude_cost.py',encoding='utf-8').read();print('plan_share gecisi:',d.count('plan_share'))"`
Expected: `1` (yalnızca tanımın kendisi) — silmeden önce. Sildikten sonra `0`.

- [ ] **Step 7: Selftest'i çalıştır**

Run: `python scripts\claude_cost.py --selftest`
Expected: `[PASS] 20. Etiketsizler dahil toplamina girmiyor, ayri sayiliyor`, `SONUC: 21/21`

- [ ] **Step 8: Elle doğrula**

Run: `python scripts\claude_cost.py --month`
Expected: Dahil / Taban+kullanım / Etiketsiz / Hariç satırları; toplam artık $20'ye çivilenmemiş

- [ ] **Step 9: Commit**

```bash
git add scripts/claude_cost.py
git commit -m "feat: aylik rapor etiket kirilimi + kullanim orani, plan_share kaldirildi"
```

---

### Task 8: `--export` / `--machine` ve import birleştirme

**Files:**
- Modify: `scripts/claude_cost.py` (`IMPORTS_DIR` sabiti; `export_sessions()`, `load_imported_sessions()`; `collect_sessions()` genişletmesi; `main()` CLI)

**Interfaces:**
- Consumes: `collect_sessions`, `parse_ts`, `to_local`
- Produces: `IMPORTS_DIR`, `EXPORT_VERSION`, `export_sessions(config, path, machine=None) -> Path`, `load_imported_sessions_from(directory, tags) -> list`; `collect_sessions`'a `include_imports=True` parametresi; CLI `--export`, `--machine`

- [ ] **Step 1: Selftest kontrollerini yaz (17, 18)**

Selftest içine 20 numaralı bloğun altına:

```python
    print("\n-- 17/18. Export-import turu ve tekillestirme --")
    import tempfile as _tf2
    _ed = Path(_tf2.mkdtemp(prefix="claude_cost_imp_"))
    try:
        _ef = _ed / "makine1.json"
        export_sessions(config, _ef, machine="testmakine")
        with open(str(_ef), "r", encoding="utf-8") as _fh:
            _payload = json.load(_fh)
        _local = collect_sessions(config, include_imports=False)
        _by_id = dict((s["session_id"], s) for s in _local)
        _same = all(
            abs(e["cost"] - _by_id[e["session_id"]]["cost"]) < 0.01
            for e in _payload["sessions"] if e["session_id"] in _by_id)
        results.append(_ok("18. Export edilen maliyet yerelde hesaplananla ayni",
                           _same and len(_payload["sessions"]) == len(_local),
                           "{} session, makine={}".format(
                               len(_payload["sessions"]), _payload["machine"])))

        # Ayni dosyayi iki farkli adla koy: tekillestirme calismali
        import shutil as _sh
        _sh.copy(str(_ef), str(_ed / "makine2.json"))
        _imported = load_imported_sessions_from(_ed, load_tags())
        _ids = [s["session_id"] for s in _imported]
        results.append(_ok("17. Ayni session iki import dosyasinda -> bir kez",
                           len(_ids) == len(set(_ids)),
                           "{} kayit, {} benzersiz".format(len(_ids), len(set(_ids)))))
    finally:
        try:
            for _f in _ed.glob("*"):
                _f.unlink()
            os.rmdir(str(_ed))
        except OSError:
            pass
```

- [ ] **Step 2: Testi çalıştır, başarısız olduğunu gör**

Run: `python scripts\claude_cost.py --selftest`
Expected: `NameError: name 'export_sessions' is not defined`

- [ ] **Step 3: Sabitleri ve importu ekle**

Dosya başındaki import bloğuna:

```python
import socket
```

`TAGS_PATH` altına:

```python
IMPORTS_DIR = Path.home() / ".claude" / "cost-imports"
EXPORT_VERSION = 1
```

- [ ] **Step 4: `export_sessions()` ve `load_imported_sessions_from()` ekle**

`collect_sessions()` fonksiyonunun altına:

```python
def export_sessions(config, path, machine=None):
    """Bu makinenin session ozetlerini yazar.

    HAM TRANSCRIPT GONDERILMEZ - yalnizca sure/token/maliyet ozeti.
    Zaman damgalari UTC ISO.

    include_imports=False SART: aksi halde bu makine, baska makinelerden
    import ettigi oturumlari kendi verisiymis gibi yeniden yayar.
    """
    sessions = collect_sessions(config, include_imports=False)
    payload = {
        "version": EXPORT_VERSION,
        "machine": machine or socket.gethostname(),
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sessions": [{
            "session_id": s["session_id"],
            "start": s["start"].isoformat(),
            "end": s["end"].isoformat(),
            "active_seconds": s["active_seconds"],
            "wall_seconds": s["wall_seconds"],
            "cwd": s["cwd"],
            "gitBranch": s["gitBranch"],
            "title": s["title"],
            "request_count": s["request_count"],
            "tokens": s["tokens"],
            "cost": s["cost"],
            "unknown_models": s["unknown_models"],
        } for s in sessions],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return path


def load_imported_sessions_from(directory, tags):
    """Klasordeki export dosyalarini okur. Bozuk dosya atlanir ve UYARILIR."""
    directory = Path(directory)
    if not directory.exists():
        return []
    out = []
    seen = set()
    for fp in sorted(directory.glob("*.json")):
        try:
            with open(str(fp), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            sys.stderr.write("UYARI: import dosyasi okunamadi, atlandi: {} ({})\n"
                             .format(fp.name, exc))
            continue
        if not isinstance(data, dict) or data.get("version") != EXPORT_VERSION:
            sys.stderr.write("UYARI: import dosyasi surumu bilinmiyor, atlandi: {}\n"
                             .format(fp.name))
            continue
        makine = data.get("machine") or fp.stem
        for e in data.get("sessions") or []:
            sid = e.get("session_id")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            start = parse_ts(e.get("start"))
            end = parse_ts(e.get("end"))
            if start is None:
                continue
            out.append({
                "session_id": sid, "title": e.get("title"), "cwd": e.get("cwd"),
                "gitBranch": e.get("gitBranch"), "slug": None, "path": str(fp),
                "machine": makine, "start": start, "end": end or start,
                "active_seconds": float(e.get("active_seconds") or 0.0),
                "wall_seconds": float(e.get("wall_seconds") or 0.0),
                "cost": float(e.get("cost") or 0.0),
                "tokens": e.get("tokens") or {}, "per_model": {},
                "request_count": int(e.get("request_count") or 0),
                "unknown_models": e.get("unknown_models") or [],
                "tag": get_tag(tags, sid),
            })
    return out
```

- [ ] **Step 5: `collect_sessions()`'a parametre ve import birleştirmesini ekle**

Önce imzayı değiştir:

```python
def collect_sessions(config, tags=None, quiet=True, include_imports=True):
```

Sonra `out.sort(...)` satırının **öncesine**:

```python
    # Import edilen makineler. Yerel veri KAZANIR: ana makinenin kendi export'u
    # klasore duserse cift sayilmaz.
    if include_imports:
        yerel_ids = set(s["session_id"] for s in out)
        for s in load_imported_sessions_from(IMPORTS_DIR, tags):
            if s["session_id"] in yerel_ids:
                continue
            if not in_tracking_scope(s["start"], config):
                continue
            out.append(s)
```

- [ ] **Step 6: CLI bayraklarını ekle**

`main()` içinde `--suggest-baseline` altına:

```python
    ap.add_argument("--export", metavar="DOSYA",
                    help="bu makinenin session ozetini yaz")
    ap.add_argument("--machine", metavar="AD",
                    help="export'a yazilacak makine adi (varsayilan: hostname)")
```

`if args.suggest_baseline:` bloğunun öncesine:

```python
    if args.export:
        written = export_sessions(config, args.export, args.machine)
        print("Export edildi: {}".format(written))
        print("  Bu dosyayi ana makinede {} klasorune kopyalayin.".format(IMPORTS_DIR))
        return 0
```

- [ ] **Step 7: Selftest'i çalıştır**

Run: `python scripts\claude_cost.py --selftest`
Expected: `[PASS] 17.` ve `[PASS] 18.`, `SONUC: 23/23`

- [ ] **Step 8: Elle doğrula**

Run: `python scripts\claude_cost.py --export "%TEMP%\test-export.json" --machine testbox`
Expected: `Export edildi: ...`; dosyada `"machine": "testbox"` ve ham konuşma metni **bulunmamalı**

- [ ] **Step 9: Commit**

```bash
git add scripts/claude_cost.py
git commit -m "feat: --export / --machine ve cost-imports birlestirme"
```

---

### Task 9: SKILL.md — doğal dil eşlemesi

**Files:**
- Modify: `skills/cost/SKILL.md`

**Interfaces:**
- Consumes: Task 1-8'de eklenen tüm CLI bayrakları
- Produces: kullanıcının python komutu yazmadan tüm işlemleri yapabildiği skill talimatları

- [ ] **Step 1: Argüman tablosunu genişlet**

`skills/cost/SKILL.md` içindeki argüman tablosuna satırları ekle:

```markdown
| "bu session'ı dahil et" / "bu müşteri işi" | `--tag $CLAUDE_CODE_SESSION_ID dahil` |
| "bunu sayma" / "kendi projem" | `--tag $CLAUDE_CODE_SESSION_ID haric` |
| "yanlış işaretledim, geri al" | `--untag <id>` |
| "etiketsizleri göster" | `--tag-list --untagged` |
| "hepsini göster" | `--tag-list` |
| "Frames'in hepsi müşteri işi" | `--tag-project "*Frames*" dahil --yes` |
| "takibi bu aydan başlat" | `--set-tracking-start <YYYY-MM>` |
| "taban ne olsun" / "taban öner" | `--suggest-baseline` |
| "tabanı 1250 yap" | `--set-baseline 1250` |
| "bu makinenin verisini dışa aktar" | `--export <dosya> --machine <ad>` |
```

- [ ] **Step 2: UUID gizleme bölümünü ekle**

`## 3. Çıktıyı sun` bölümünden önce yeni bölüm ekle:

```markdown
## 2b. Etiketleme akışı — kullanıcıya UUID yazdırma

`--tag-list` çıktısı UUID içerir ama **kullanıcı onları kopyalamaz.** Listeyi
numaralandırarak sun:

    1. 16 Ağu 18:34  Brainstorm superpowers…   43dk   $34,55   ~/
    2. 15 Ağu 09:12  Frames watchlist algo     2sa    $187,20  Frames

Kullanıcı "1 ve 3 dahil, 2 hariç" der; sen numaraları UUID'lere çevirip
`--tag` çağrılarını **kendin** yaparsın. Numaralar yalnızca o listeleme
içinde geçerlidir — her seferinde yeniden listele, eski numaraya güvenme.

"Bu session" denince `CLAUDE_CODE_SESSION_ID` ortam değişkenini kullan;
kullanıcıdan ID isteme.

**Toplu etiketlemede önce onay al.** `--tag-project` çok sayıda oturumu
etkiler. Komutu `--yes` olmadan çalıştır, kaç oturumun etkileneceğini
kullanıcıya söyle, onay gelince `--yes` ile tekrarla.
```

- [ ] **Step 3: Bilinmesi gerekenler bölümünü güncelle**

`- **Plan payı** = ...` maddesini şununla değiştir:

```markdown
- **Plan maliyeti sabittir.** `session_API_karşılığı ÷ TABAN × plan_tutarı`.
  Payda ay toplamı değil **dondurulmuş taban** olduğu için rakam session
  bitiminde sabitlenir ve sonradan açılan oturumlardan etkilenmez.
- **Taban set edilmemişse** rapor sayı üretmez. Önce `--suggest-baseline`
  ile göster, kullanıcı karar versin, sonra `--set-baseline`. Sen rakam seçme.
- **Etiketsiz session** plan maliyetine katılmaz ve raporda ayrı görünür.
  Kullanıcıya etiketlemesini hatırlat.
- **Aylık toplam artık $20 etmez.** Kullanım oranı gösterilir: %130 "planı
  fazlasıyla kullanıyorum", %40 "az kullanıyorum" demek. Bu hata değil.
```

- [ ] **Step 4: Elle doğrula**

Run: `python -c "import re,io;d=open(r'skills/cost/SKILL.md',encoding='utf-8').read();m=re.match(r'---\n(.*?)\n---',d,re.S);print('frontmatter ok:',bool(m));print('yeni bayraklar:',all(k in d for k in ('--tag-list','--suggest-baseline','--set-tracking-start','--export')))"`
Expected: `frontmatter ok: True`, `yeni bayraklar: True`

- [ ] **Step 5: Commit**

```bash
git add skills/cost/SKILL.md
git commit -m "docs: SKILL.md dogal dil eslemesi, UUID gizleme, toplu onay akisi"
```

---

### Task 10: README + sürüm 2.0.0

**Files:**
- Modify: `README.md`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `scripts/claude_cost.py` (`__version__`)

**Interfaces:**
- Consumes: Task 1-9
- Produces: sürüm 2.0.0, güncel kullanım dokümanı

- [ ] **Step 1: Sürümü üç yerde birden yükselt**

`scripts/claude_cost.py` satır 24: `__version__ = "2.0.0"`
`.claude-plugin/plugin.json`: `"version": "2.0.0"`
`.claude-plugin/marketplace.json`: `"version": "2.0.0"`

- [ ] **Step 2: README'nin plan payı bölümünü değiştir**

`## İki maliyet modeli neden var?` bölümünü şununla değiştir:

```markdown
## İki maliyet modeli neden var?

- **A) API-karşılığı maliyet** — session'ın token kullanımının API liste
  fiyatındaki karşılığı. Abonelik planında bu tutar tahsil edilmez; session'ın
  "ağırlığını" ölçen mutlak bir rakamdır.
- **B) Plan maliyeti (sabit oran)** — `session_API_karşılığı ÷ TABAN × plan`.

**Neden taban?** Önceki sürümde payda ayın o ana kadarki toplamıydı; ay
ilerledikçe aynı session'ın rakamı düşüyordu. Session bitiminde sabit bir sayı
alınamıyor, oturumlar ve aylar karşılaştırılamıyordu.

Bir rakam **aynı anda** hem session bitince sabitlenip hem ay sonunda toplamı
tam plan tutarını edemez — session bitiminde kaç oturum daha açılacağı
bilinmez. **Sabitlik seçildi.** Aylık toplamın $20'den sapması bilgidir:
%130 "planı fazlasıyla kullanıyorum", %40 "az kullanıyorum".
```

- [ ] **Step 3: README'ye etiketleme bölümü ekle**

`## Config` bölümünden önce ekle:

```markdown
## Hangi session'lar sayılıyor

Claude hem müşteri işlerinde hem kendi projelerinde kullanılıyor. Yalnızca
**etiketlenmiş** oturumlar plan maliyetine girer:

- `dahil` — müşteri işi, hesaba katılır
- `haric` — kendi projen, katılmaz
- **etiketsiz** — üçüncü durum: ne dahil ne hariç, raporda ayrı satırda
  görünür. Etiketlemeyi unutursan rakam sessizce yanlış çıkmaz.

`tracking_start_month` ile takibin başladığı ay belirlenir; öncesindeki
oturumlar hiç görünmez.

### Çok makineli kullanım

Her makinede `--export`, dosyaları ana makinede `~/.claude/cost-imports/`
klasörüne koy. Rapor hepsini birleştirir, `session_id` ile tekilleştirir.
Export'ta **ham konuşma yoktur** — yalnızca süre/token/maliyet özeti.
```

- [ ] **Step 4: Selftest'i son kez çalıştır**

Run: `python scripts\claude_cost.py --selftest`
Expected: tüm kontroller PASS, `SONUC: 23/23 kontrol gecti`

(15 mevcut + 8 yeni: 16, 16b, 17, 18, 19, 20, 21, 22)

- [ ] **Step 5: Sürüm hizasını doğrula**

Run: `python -c "import json;print(json.load(open('.claude-plugin/plugin.json',encoding='utf-8'))['version'], json.load(open('.claude-plugin/marketplace.json',encoding='utf-8'))['plugins'][0]['version'])"` ve `python scripts\claude_cost.py --version`
Expected: üçü de `2.0.0`

- [ ] **Step 6: Commit ve push**

```bash
git add -A
git commit -m "release: 2.0.0 - sabit oranli plan maliyeti, etiketleme, cok makine"
git push origin main
```
