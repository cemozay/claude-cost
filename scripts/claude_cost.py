#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""claude_cost.py - Claude Code session sure + maliyet raporu.

Python 3.8+, yalnizca stdlib. Windows ve Linux'ta ayni sekilde calisir.
3.8 tabani: `match`, `list[str]`, `X | Y` sozdizimi KULLANILMAZ.

Veri kaynagi: <home>/.claude/projects/<proje-slug>/<session-id>.jsonl

EN KRITIK NOKTA: tek bir API yaniti, icerdigi her content block icin ayri bir
JSONL satiri olarak yazilir ve her satir AYNI usage nesnesini tasir. Bu yuzden
token'lar `requestId` bazinda tekillestirilir; yapilmazsa tum rakamlar sisik cikar.
`--selftest` bu varsayimi yerel veriye karsi ayrica kanitlar.
"""

import argparse
import json
import os
import sys
import datetime
import fnmatch

from pathlib import Path

__version__ = "1.1.0"

# ---------------------------------------------------------------- sabitler

CONFIG_PATH = Path.home() / ".claude" / "cost-config.json"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
TAGS_PATH = Path.home() / ".claude" / "cost-tags.json"

# Fiyatlar config dosyasinda tutulur ki fiyat degisince kod degismesin.
# Cache fiyatlari input fiyatinin carpani olarak hesaplanir (API'nin gercek modeli budur).
DEFAULT_CONFIG = {
    "plan": {"amount": 20.0, "currency": "USD", "label": "Pro"},
    "idle_gap_seconds": 300,
    # Bu aydan ONCE baslayan session'lar hic gorunmez: rapora girmez,
    # --tag-list'te listelenmez, "etiketsiz" uyarisi uretmez.
    # None = tum gecmis kapsamda (geriye uyumluluk).
    "tracking_start_month": None,
    # Dondurulmus taban. ASLA kendiliginden degismez; yalnizca --set-baseline yazar.
    # None ise rapor sayi uretmez, hata verir.
    "baseline_monthly_api_cost": None,
    "baseline_source": None,
    "pricing_per_mtok": {
        "claude-fable-5":   {"input": 10.0, "output": 50.0},
        "claude-mythos-5":  {"input": 10.0, "output": 50.0},
        "claude-opus-5":    {"input": 5.0,  "output": 25.0},
        "claude-opus-4-8":  {"input": 5.0,  "output": 25.0},
        "claude-opus-4-7":  {"input": 5.0,  "output": 25.0},
        "claude-opus-4-6":  {"input": 5.0,  "output": 25.0},
        "claude-sonnet-5":  {"input": 3.0,  "output": 15.0},
        "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
        "claude-haiku-4-5": {"input": 1.0,  "output": 5.0},
        # Gercek bir API cagrisi degil; tum token'lari sifir. Bilinmeyen model
        # uyarisi vermemesi icin acikca 0 fiyatla tanimli.
        "<synthetic>":      {"input": 0.0,  "output": 0.0},
    },
    "cache_multipliers": {"write_5m": 1.25, "write_1h": 2.0, "read": 0.1},
}

# Kisa takma adlar (transcript'te bazi kayitlarda tam ad yerine bunlar gecebilir).
MODEL_ALIASES = {
    "fable": "claude-fable-5",
    "mythos": "claude-mythos-5",
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}

TR_MONTHS = {
    1: "Ocak", 2: "Subat", 3: "Mart", 4: "Nisan", 5: "Mayis", 6: "Haziran",
    7: "Temmuz", 8: "Agustos", 9: "Eylul", 10: "Ekim", 11: "Kasim", 12: "Aralik",
}


# ---------------------------------------------------------------- yardimcilar

def _setup_stdout():
    """Windows konsolunda Turkce karakterler patlamasin."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def fmt_int(n):
    """12400 -> '12.400' (binlik ayraci nokta)."""
    return "{:,}".format(int(n)).replace(",", ".")


def fmt_money(x, currency="USD"):
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "TRY": "₺"}.get(currency, "")
    if sym:
        return "{}{:,.2f}".format(sym, x)
    return "{:,.2f} {}".format(x, currency)


def fmt_dur(seconds):
    """9062 -> '2sa 31dk 02sn'; 1502 -> '25dk 02sn'."""
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "{}sa {:02d}dk {:02d}sn".format(h, m, s)
    return "{}dk {:02d}sn".format(m, s)


def fmt_threshold(seconds):
    """Esik gosterimi: tam dakikaysa '5dk', degilse '90sn' / '1dk 30sn'."""
    seconds = float(seconds)
    if seconds >= 60 and abs(seconds % 60) < 1e-9:
        return "{:g}dk".format(seconds / 60)
    if seconds < 60:
        return "{:g}sn".format(seconds)
    return fmt_dur(seconds)


def parse_ts(raw):
    """'2026-07-12T03:21:34.996Z' -> aware datetime.

    Python 3.8 fromisoformat 'Z' son ekini AYRISTIRAMAZ; '+00:00'a cevriliyor.
    """
    if not raw or not isinstance(raw, str):
        return None
    txt = raw.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        return datetime.datetime.fromisoformat(txt)
    except ValueError:
        return None


def to_local(dt):
    return dt.astimezone() if dt is not None else None


# ---------------------------------------------------------------- 1. config

def load_config(path=None):
    """Config yoksa varsayilani yazip doner."""
    path = Path(path) if path else CONFIG_PATH
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(path), "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        except OSError:
            pass  # yazamazsak da varsayilanla devam et
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        return cfg, path

    try:
        with open(str(path), "r", encoding="utf-8") as f:
            user = json.load(f)
    except (OSError, ValueError) as exc:
        sys.stderr.write("UYARI: config okunamadi ({}), varsayilan kullaniliyor: {}\n"
                         .format(path, exc))
        return json.loads(json.dumps(DEFAULT_CONFIG)), path

    # Eksik anahtarlari varsayilandan tamamla (eski config dosyalari kirilmasin).
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for key, val in user.items():
        if isinstance(val, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(val)
        else:
            cfg[key] = val
    return cfg, path


def save_config(cfg, path=None):
    path = Path(path) if path else CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return path


# ---------------------------------------------------------------- 1b. etiketler

# True = dahil, False = haric, anahtar YOK = etiketsiz.
TAG_LABELS = {True: "dahil", False: "haric", None: "etiketsiz"}


def parse_tag_word(word):
    """'dahil'/'haric' -> True/False. Taninmazsa None."""
    w = (word or "").strip().lower()
    if w in ("dahil", "include", "in"):
        return True
    if w in ("haric", "exclude", "out"):
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


# ---------------------------------------------------------------- 2. dosya bulma

def find_session_file(session_id=None):
    """Sirayla: --session argumani -> CLAUDE_CODE_SESSION_ID -> en yeni .jsonl.

    Proje slug'i cwd'den TURETILMEZ: Windows yol -> slug donusumu belirsiz ve
    kirilgan. Dosya dogrudan projects/*/<session-id>.jsonl glob'u ile aranir.
    """
    sid = session_id or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if sid:
        matches = sorted(PROJECTS_DIR.glob("*/{}.jsonl".format(sid)))
        if matches:
            return matches[0], "session-id"
        sys.stderr.write("UYARI: '{}' icin transcript bulunamadi; en yeni dosyaya "
                         "duseluyor.\n".format(sid))

    files = list(PROJECTS_DIR.glob("*/*.jsonl"))
    if not files:
        return None, "yok"
    newest = max(files, key=lambda p: p.stat().st_mtime)
    return newest, "en-yeni"


def all_session_files():
    return sorted(PROJECTS_DIR.glob("*/*.jsonl"))


# ---------------------------------------------------------------- 3. ayristirma

def parse_session(path):
    """JSONL'i satir satir okur. Bozuk/bos satirlari atlar.

    Doner: {"requests": {reqId: {"usage":..., "model":...}}, "timestamps": [...],
            "meta": {...}, "bad_lines": int}
    """
    requests = {}
    timestamps = []
    meta = {"slug": Path(path).parent.name, "cwd": None, "gitBranch": None,
            "sessionId": None, "title": None, "path": str(path)}
    bad_lines = 0

    # encoding="utf-8" sart: Windows varsayilani cp1254/cp1252 olur ve
    # Turkce karakterlerde coker.
    with open(str(path), "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                bad_lines += 1
                continue
            if not isinstance(rec, dict):
                bad_lines += 1
                continue

            ts = parse_ts(rec.get("timestamp"))
            if ts is not None:
                timestamps.append(ts)

            for key in ("cwd", "gitBranch", "sessionId"):
                if meta[key] is None and rec.get(key):
                    meta[key] = rec[key]
            for key in ("customTitle", "aiTitle", "title"):
                if rec.get(key):
                    meta["title"] = rec[key]
                    break

            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue

            # Sidechain (subagent) kayitlari DAHIL edilir - token harciyorlar.
            # requestId ILK gorulduğunde alinir, sonraki tekrarlari atlanir.
            # Sismeyi onleyen kritik adim budur.
            rid = rec.get("requestId") or rec.get("uuid")
            if not rid or rid in requests:
                continue
            requests[rid] = {"usage": usage, "model": msg.get("model")}

    timestamps.sort()
    return {"requests": requests, "timestamps": timestamps,
            "meta": meta, "bad_lines": bad_lines}


# ---------------------------------------------------------------- 4. sure

def compute_duration(timestamps, idle_gap):
    """wall = son - ilk; active = esigi asmayan ardisik farklarin toplami."""
    if len(timestamps) < 2:
        return {"wall": 0.0, "active": 0.0, "idle": 0.0, "gaps": 0,
                "gap_list": [],
                "start": timestamps[0] if timestamps else None,
                "end": timestamps[0] if timestamps else None}

    wall = (timestamps[-1] - timestamps[0]).total_seconds()
    active = 0.0
    idle = 0.0
    gap_list = []
    for i in range(1, len(timestamps)):
        delta = (timestamps[i] - timestamps[i - 1]).total_seconds()
        if delta > idle_gap:
            idle += delta
            # Hangi aralarin dusuldugu denetlenebilsin diye kaydedilir:
            # freelance faturalamada rakamin savunulabilir olmasi gerekir.
            gap_list.append({"at": timestamps[i - 1], "seconds": delta})
        else:
            active += delta
    gap_list.sort(key=lambda g: -g["seconds"])
    return {"wall": wall, "active": active, "idle": idle, "gaps": len(gap_list),
            "gap_list": gap_list,
            "start": timestamps[0], "end": timestamps[-1]}


# ---------------------------------------------------------------- 5. fiyatlama

def resolve_price(model, pricing):
    """Once tam eslesme, sonra takma ad, sonra en uzun on-ek eslesmesi."""
    if not model:
        return None, None
    if model in pricing:
        return pricing[model], model
    alias = MODEL_ALIASES.get(model)
    if alias and alias in pricing:
        return pricing[alias], alias
    best = None
    for key in pricing:
        if model.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    if best:
        return pricing[best], best
    return None, None


def _tokens_from_usage(usage):
    """cache_creation detayi varsa onu, yoksa toplami 5dk kabul et."""
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    read = int(usage.get("cache_read_input_tokens") or 0)
    total_write = int(usage.get("cache_creation_input_tokens") or 0)

    detail = usage.get("cache_creation")
    if isinstance(detail, dict):
        w5 = int(detail.get("ephemeral_5m_input_tokens") or 0)
        w1h = int(detail.get("ephemeral_1h_input_tokens") or 0)
        if w5 + w1h == 0 and total_write:
            w5 = total_write
    else:
        w5, w1h = total_write, 0
    return {"input": inp, "output": out, "cache_read": read,
            "cache_write_5m": w5, "cache_write_1h": w1h}


def price_requests(requests, config):
    """Model bazinda gruplar ve maliyeti hesaplar.

    maliyet = input      x in$/1M
            + output     x out$/1M
            + cache_5m   x in$/1M x 1.25
            + cache_1h   x in$/1M x 2.0
            + cache_read x in$/1M x 0.1
    """
    pricing = config.get("pricing_per_mtok", {})
    mult = config.get("cache_multipliers", DEFAULT_CONFIG["cache_multipliers"])
    m5 = float(mult.get("write_5m", 1.25))
    m1h = float(mult.get("write_1h", 2.0))
    mr = float(mult.get("read", 0.1))

    per_model = {}
    totals = {"input": 0, "output": 0, "cache_read": 0,
              "cache_write_5m": 0, "cache_write_1h": 0}
    unknown = {}
    total_cost = 0.0

    for entry in requests.values():
        model = entry.get("model") or "(bilinmiyor)"
        tok = _tokens_from_usage(entry.get("usage") or {})
        for k in totals:
            totals[k] += tok[k]

        slot = per_model.setdefault(model, {
            "requests": 0, "cost": 0.0, "priced": True, "price_key": None,
            "input": 0, "output": 0, "cache_read": 0,
            "cache_write_5m": 0, "cache_write_1h": 0,
        })
        slot["requests"] += 1
        for k in totals:
            slot[k] += tok[k]

        price, key = resolve_price(model, pricing)
        if price is None:
            slot["priced"] = False
            unknown[model] = unknown.get(model, 0) + 1
            continue
        slot["price_key"] = key

        pin = float(price.get("input", 0.0)) / 1e6
        pout = float(price.get("output", 0.0)) / 1e6
        cost = (tok["input"] * pin
                + tok["output"] * pout
                + tok["cache_write_5m"] * pin * m5
                + tok["cache_write_1h"] * pin * m1h
                + tok["cache_read"] * pin * mr)
        slot["cost"] += cost
        total_cost += cost

    # Fiyat tablosunda olmayan model SESSIZCE 0 sayilmaz.
    for model, count in sorted(unknown.items()):
        sys.stderr.write("WARN: bilinmeyen model {} ({} istek) - maliyeti "
                         "hesaplanamadi\n".format(model, count))

    return {"per_model": per_model, "totals": totals, "total_cost": total_cost,
            "unknown_models": unknown, "request_count": len(requests)}


# ---------------------------------------------------------------- 6. aylik

def month_key(dt):
    return to_local(dt).strftime("%Y-%m")


def in_tracking_scope(start_dt, config):
    """Session takip kapsaminda mi? month_key 'YYYY-MM' dondugu icin
    dize karsilastirmasi kronolojik siralamayla ayni sonucu verir."""
    start_month = config.get("tracking_start_month")
    if not start_month:
        return True
    if start_dt is None:
        return False
    return month_key(start_dt) >= start_month


def collect_sessions(config, tags=None, quiet=True):
    """Kapsamdaki tum session'larin ozetini toplar.

    Takip baslangicindan onceki session'lar tamamen elenir.
    """
    if tags is None:
        tags = load_tags()
    out = []
    stderr = sys.stderr
    if quiet:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
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


def month_totals(month, config, quiet=True):
    """projects/*/*.jsonl taranir; her session BASLANGIC ayina gore gruplanir.

    Tum projeler dahil (freelance birden cok repoda calisiyor).
    """
    sessions = []
    stderr = sys.stderr
    if quiet:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # tarama sirasinda WARN spam'ini bastir
    try:
        for path in all_session_files():
            parsed = parse_session(path)
            if not parsed["timestamps"] or not parsed["requests"]:
                continue
            start = parsed["timestamps"][0]
            if month_key(start) != month:
                continue
            priced = price_requests(parsed["requests"], config)
            sessions.append({
                "path": str(path),
                "session_id": parsed["meta"].get("sessionId") or Path(path).stem,
                "title": parsed["meta"].get("title"),
                "slug": parsed["meta"].get("slug"),
                "start": start,
                "cost": priced["total_cost"],
                "requests": priced["request_count"],
                "unknown_models": priced["unknown_models"],
            })
    finally:
        if quiet:
            sys.stderr.close()
            sys.stderr = stderr

    sessions.sort(key=lambda s: s["start"])
    total = sum(s["cost"] for s in sessions)
    return {"month": month, "sessions": sessions, "total_cost": total,
            "session_count": len(sessions)}


# ---------------------------------------------------------------- 7. plan payi

def plan_share(session_cost, month_cost, plan_amount):
    """Agirlik ham token degil, API-karsiligi MALIYET.

    Output token input'un 5 katı maliyetli oldugu icin tek savunulabilir
    dagitim anahtari budur.
    """
    if month_cost <= 0:
        return {"ratio": 0.0, "amount": 0.0}
    ratio = session_cost / month_cost
    return {"ratio": ratio, "amount": ratio * plan_amount}


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


def is_current_month(month):
    return month == datetime.datetime.now().strftime("%Y-%m")


# ---------------------------------------------------------------- 8. cikti

def render_text(report):
    L = []
    cur = report["plan"]["currency"]

    if report["mode"] == "session":
        s = report["session"]
        d = report["duration"]
        L.append("Session:   {}".format(s["title"] or s["session_id"]))
        proje = s.get("cwd") or s.get("slug") or "(bilinmiyor)"
        if s.get("gitBranch"):
            proje += "  ({})".format(s["gitBranch"])
        L.append("Proje:     {}".format(proje))
        if d["start"] and d["end"]:
            st, en = to_local(d["start"]), to_local(d["end"])
            L.append("Baslangic: {} {} {}   Bitis: {}".format(
                st.day, TR_MONTHS[st.month], st.strftime("%Y %H:%M"),
                en.strftime("%d %b %Y %H:%M") if st.date() != en.date()
                else en.strftime("%H:%M")))
        L.append("")
        L.append("Sure")
        L.append("  Duvar saati : {}".format(fmt_dur(d["wall"])))
        L.append("  Aktif       : {}   ({} bosluk haric, esik {})".format(
            fmt_dur(d["active"]), fmt_dur(d["idle"]),
            fmt_threshold(report["idle_gap"])))
        gl = d.get("gap_list") or []
        if gl:
            L.append("    haric tutulan aralar ({} adet):".format(len(gl)))
            for g in gl[:6]:
                L.append("      {}  {:>14}".format(
                    to_local(g["at"]).strftime("%d %b %H:%M"), fmt_dur(g["seconds"])))
            if len(gl) > 6:
                L.append("      ... {} ara daha".format(len(gl) - 6))
        L.append("")

        t = report["tokens"]
        L.append("Token (requestId ile tekillestirilmis, {} istek)".format(
            report["request_count"]))
        L.append("  input          {:>14}      cache write 5dk  {:>14}".format(
            fmt_int(t["input"]), fmt_int(t["cache_write_5m"])))
        L.append("  output         {:>14}      cache write 1sa  {:>14}".format(
            fmt_int(t["output"]), fmt_int(t["cache_write_1h"])))
        L.append("  cache read     {:>14}".format(fmt_int(t["cache_read"])))
        L.append("")

        L.append("A) API-karsiligi maliyet")
        for model, slot in sorted(report["per_model"].items(),
                                  key=lambda kv: -kv[1]["cost"]):
            mark = "" if slot["priced"] else "   <-- FIYAT YOK"
            L.append("  {:<20} {:>10}{}".format(
                model, fmt_money(slot["cost"], cur), mark))
        L.append("  {:<20} {:>10}".format("TOPLAM",
                                          fmt_money(report["total_cost"], cur)))
        plan = report["plan"]
        if plan["amount"] > 0:
            pct = report["total_cost"] / plan["amount"] * 100.0
            L.append("  {:<20} {:>10}  -> {} plan {} tutarinin %{:.1f}'i".format(
                "", "", plan.get("label", ""), fmt_money(plan["amount"], cur), pct))
        L.append("")

        m = report.get("month_context")
        if m:
            mk = m["month"]
            y, mo = mk.split("-")
            etiket = "  (ay ici, gecici)" if is_current_month(mk) else ""
            L.append("B) Plan payi - {} {}{}".format(TR_MONTHS[int(mo)], y, etiket))
            L.append("  Bu ay: {} session, toplam API-karsiligi {}".format(
                m["session_count"], fmt_money(m["total_cost"], cur)))
            L.append("  Bu session payi: %{:.1f}  ->  {} / {}".format(
                m["share"]["ratio"] * 100.0,
                fmt_money(m["share"]["amount"], cur),
                fmt_money(plan["amount"], cur)))
    else:
        m = report["month"]
        mk = m["month"]
        y, mo = mk.split("-")
        etiket = "  (ay ici, gecici)" if is_current_month(mk) else ""
        plan = report["plan"]
        L.append("Aylik ozet - {} {}{}".format(TR_MONTHS[int(mo)], y, etiket))
        L.append("Plan: {} {}".format(plan.get("label", ""),
                                      fmt_money(plan["amount"], cur)))
        L.append("")
        L.append("  {:<40} {:>10} {:>8} {:>10}".format(
            "Session", "API-karsl.", "Pay", "Plan payi"))
        for s in m["sessions"]:
            share = plan_share(s["cost"], m["total_cost"], plan["amount"])
            name = (s["title"] or s["session_id"])[:38]
            L.append("  {:<40} {:>10} {:>7.1f}% {:>10}".format(
                name, fmt_money(s["cost"], cur), share["ratio"] * 100.0,
                fmt_money(share["amount"], cur)))
        L.append("")
        L.append("  {:<40} {:>10} {:>8} {:>10}".format(
            "TOPLAM ({} session)".format(m["session_count"]),
            fmt_money(m["total_cost"], cur), "100.0%",
            fmt_money(plan["amount"] if m["total_cost"] > 0 else 0.0, cur)))

    if report.get("unknown_models"):
        L.append("")
        L.append("EKSIK: su modellerin fiyati config'de yok, maliyete DAHIL "
                 "EDILMEDI: {}".format(", ".join(sorted(report["unknown_models"]))))
        L.append("       {} dosyasindaki pricing_per_mtok'a ekleyin.".format(
            report.get("config_path", str(CONFIG_PATH))))
    if report.get("bad_lines"):
        L.append("")
        L.append("Not: {} bozuk/okunamayan satir atlandi.".format(report["bad_lines"]))
    return "\n".join(L)


def render_json(report):
    def default(o):
        if isinstance(o, datetime.datetime):
            return o.isoformat()
        if isinstance(o, Path):
            return str(o)
        return str(o)
    return json.dumps(report, indent=2, ensure_ascii=False, default=default)


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


# ---------------------------------------------------------------- rapor kurma

def build_session_report(path, config, config_path, with_month=True):
    parsed = parse_session(path)
    idle_gap = float(config.get("idle_gap_seconds", 300))
    duration = compute_duration(parsed["timestamps"], idle_gap)
    priced = price_requests(parsed["requests"], config)
    plan = config.get("plan", DEFAULT_CONFIG["plan"])

    report = {
        "mode": "session",
        "version": __version__,
        "config_path": str(config_path),
        "plan": plan,
        "idle_gap": idle_gap,
        "session": {
            "session_id": parsed["meta"].get("sessionId") or Path(path).stem,
            "title": parsed["meta"].get("title"),
            "cwd": parsed["meta"].get("cwd"),
            "gitBranch": parsed["meta"].get("gitBranch"),
            "slug": parsed["meta"].get("slug"),
            "path": str(path),
        },
        "duration": duration,
        "tokens": priced["totals"],
        "per_model": priced["per_model"],
        "total_cost": priced["total_cost"],
        "request_count": priced["request_count"],
        "unknown_models": sorted(priced["unknown_models"]),
        "bad_lines": parsed["bad_lines"],
    }

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


def build_month_report(month, config, config_path):
    totals = month_totals(month, config)
    plan = config.get("plan", DEFAULT_CONFIG["plan"])
    unknown = set()
    for s in totals["sessions"]:
        unknown.update(s.get("unknown_models") or {})
    return {
        "mode": "month",
        "version": __version__,
        "config_path": str(config_path),
        "plan": plan,
        "month": totals,
        "unknown_models": sorted(unknown),
    }


# ---------------------------------------------------------------- selftest

def _ok(label, passed, detail=""):
    mark = "PASS" if passed else "FAIL"
    print("[{}] {}{}".format(mark, label, ("  " + detail) if detail else ""))
    return passed


def selftest(config, config_path):
    print("=" * 72)
    print("claude_cost {} - selftest (yerel veriye karsi kendini dogrular)".format(__version__))
    print("=" * 72)
    results = []

    files = all_session_files()
    if not files:
        print("FAIL: {} altinda hic .jsonl yok.".format(PROJECTS_DIR))
        return 1
    print("Transcript dizini: {}  ({} dosya)\n".format(PROJECTS_DIR, len(files)))

    biggest = max(files, key=lambda p: p.stat().st_size)
    print("-- 1/2. Dedup varsayiminin kaniti + sismenin olculmesi --")
    print("Dosya: {}  ({:.2f} MB)".format(biggest.name, biggest.stat().st_size / 1e6))

    # Ayni requestId'ye sahip TUM satirlarin usage nesneleri toplanir.
    by_req = {}
    with open(str(biggest), "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
                continue
            rid = rec.get("requestId")
            if rid:
                by_req.setdefault(rid, []).append(msg["usage"])

    dupes = dict((r, u) for r, u in by_req.items() if len(u) > 1)
    identical = sum(1 for u in dupes.values() if all(x == u[0] for x in u))
    results.append(_ok(
        "1. Tekrar eden requestId'lerin usage'i birebir ayni",
        identical == len(dupes),
        "{}/{} tekrar eden requestId".format(identical, len(dupes))))
    if identical != len(dupes):
        print("     -> Dedup stratejisi YANLIS. Durduruluyor.")
        return 1

    naive = sum(u.get("output_tokens", 0) for us in by_req.values() for u in us)
    dedup = sum(us[0].get("output_tokens", 0) for us in by_req.values())
    ratio = naive / dedup if dedup else 1.0
    results.append(_ok(
        "2. Sisme orani >= 1 (tekrar varsa > 1)",
        ratio >= 1.0 and (ratio > 1.0 or not dupes),
        "naif {} / dedup {} = {:.2f}x".format(fmt_int(naive), fmt_int(dedup), ratio)))

    print("\n-- 3. Sure tutarliligi --")
    parsed = parse_session(biggest)
    ts = parsed["timestamps"]
    gap = float(config.get("idle_gap_seconds", 300))
    d = compute_duration(ts, gap)
    results.append(_ok("3a. active <= wall", d["active"] <= d["wall"] + 1e-6,
                       "{:.1f}s <= {:.1f}s".format(d["active"], d["wall"])))
    results.append(_ok("3b. wall - active == bosluklarin toplami (+-1sn)",
                       abs((d["wall"] - d["active"]) - d["idle"]) <= 1.0,
                       "fark {:.3f}s".format(abs((d["wall"] - d["active"]) - d["idle"]))))
    huge = compute_duration(ts, 10 ** 9)
    results.append(_ok("3c. esik 10^9 iken active == wall",
                       abs(huge["active"] - huge["wall"]) <= 1.0,
                       "{:.1f}s vs {:.1f}s".format(huge["active"], huge["wall"])))
    results.append(_ok("3d. haric tutulan aralarin toplami == idle",
                       abs(sum(g["seconds"] for g in d["gap_list"]) - d["idle"]) <= 0.001,
                       "{} ara, toplam {:.1f}s".format(len(d["gap_list"]), d["idle"])))
    # Esik buyudukce daha az bosluk dusulur -> aktif sure monoton ARTAR.
    ladder = [(g, compute_duration(ts, g)) for g in (60, 300, 900, 3600)]
    mono = all(ladder[i][1]["active"] >= ladder[i - 1][1]["active"] - 0.001
               for i in range(1, len(ladder)))
    results.append(_ok("3e. esik buyudukce aktif sure monoton artiyor", mono,
                       "  ".join("{}dk={}".format(g // 60, fmt_dur(r["active"]))
                                for g, r in ladder)))

    print("\n-- 4. Session bulma --")
    env_sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if env_sid:
        found, how = find_session_file()
        sub = parse_session(found) if found else None
        match = bool(sub and sub["meta"].get("sessionId") == env_sid)
        results.append(_ok("4a. CLAUDE_CODE_SESSION_ID ile bulunan sessionId eslesiyor",
                           match, "{} ({})".format(env_sid, how)))
    else:
        print("[SKIP] 4a. CLAUDE_CODE_SESSION_ID ayarli degil")
    saved = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
    try:
        f2, how2 = find_session_file()
        results.append(_ok("4b. env yokken en-yeni-dosya yoluna dusuyor",
                           f2 is not None and how2 == "en-yeni",
                           f2.name if f2 else "-"))
    finally:
        if saved is not None:
            os.environ["CLAUDE_CODE_SESSION_ID"] = saved

    print("\n-- 5. Fiyatlama (elle carpimla karsilastirma) --")
    priced = price_requests(parsed["requests"], config)
    mult = config["cache_multipliers"]
    ok5 = True
    for model, slot in priced["per_model"].items():
        if not slot["priced"]:
            continue
        p = config["pricing_per_mtok"][slot["price_key"]]
        manual = (slot["input"] * p["input"] / 1e6
                  + slot["output"] * p["output"] / 1e6
                  + slot["cache_write_5m"] * p["input"] / 1e6 * mult["write_5m"]
                  + slot["cache_write_1h"] * p["input"] / 1e6 * mult["write_1h"]
                  + slot["cache_read"] * p["input"] / 1e6 * mult["read"])
        good = abs(manual - slot["cost"]) < 0.01
        ok5 = ok5 and good
        print("     {:<20} script {:>9.4f}  elle {:>9.4f}  {}".format(
            model, slot["cost"], manual, "ok" if good else "FARKLI"))
    results.append(_ok("5. Model maliyetleri elle carpimla ayni", ok5))

    print("\n-- 6. Bilinmeyen model --")
    used = [m for m, s in priced["per_model"].items() if s["priced"]]
    if used:
        victim = used[0]
        cfg2 = json.loads(json.dumps(config))
        key = priced["per_model"][victim]["price_key"]
        cfg2["pricing_per_mtok"].pop(key, None)
        # Prefix/alias ile geri eslesmesin diye onekleri de temizle
        for k in list(cfg2["pricing_per_mtok"]):
            if victim.startswith(k):
                cfg2["pricing_per_mtok"].pop(k)
        p2 = price_requests(parsed["requests"], cfg2)
        results.append(_ok("6. Fiyati silinen model sessizce 0 sayilmiyor (WARN + eksik)",
                           victim in p2["unknown_models"] and p2["total_cost"] < priced["total_cost"],
                           "{} -> unknown, maliyet {:.2f} -> {:.2f}".format(
                               victim, priced["total_cost"], p2["total_cost"])))
    else:
        print("[SKIP] 6. fiyatlanabilen model yok")

    print("\n-- 7. Aylik + plan payi --")
    mk = month_key(d["start"]) if d["start"] else datetime.datetime.now().strftime("%Y-%m")
    mt = month_totals(mk, config)
    plan_amt = float(config["plan"]["amount"])
    shares = [plan_share(s["cost"], mt["total_cost"], plan_amt)["amount"]
              for s in mt["sessions"]]
    total_share = sum(shares)
    results.append(_ok("7. Tum session paylarinin toplami = plan tutari (+-0.01)",
                       mt["total_cost"] <= 0 or abs(total_share - plan_amt) <= 0.01,
                       "{} session, toplam pay {:.4f} vs plan {:.2f}".format(
                           mt["session_count"], total_share, plan_amt)))

    print("\n-- 8. Plan override --")
    cfg3 = json.loads(json.dumps(config))
    cfg3["plan"]["amount"] = plan_amt * 5
    s_before = plan_share(1.0, 10.0, plan_amt)
    s_after = plan_share(1.0, 10.0, plan_amt * 5)
    results.append(_ok("8. Plan 5x iken yuzde sabit, tutar 5x",
                       abs(s_before["ratio"] - s_after["ratio"]) < 1e-9
                       and abs(s_after["amount"] - s_before["amount"] * 5) < 1e-9,
                       "oran {:.4f} sabit, tutar {:.2f} -> {:.2f}".format(
                           s_before["ratio"], s_before["amount"], s_after["amount"])))

    print("\n-- 9. Bozuk veri dayanikliligi --")
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="claude_cost_")
    tmp = Path(tmpdir) / "broken.jsonl"
    with open(str(biggest), "r", encoding="utf-8", errors="replace") as src:
        lines = src.readlines()[:400]
    lines += ["\n", "{bu gecerli json degil\n", "\n", "null\n", "12345\n"]
    with open(str(tmp), "w", encoding="utf-8") as dst:
        dst.writelines(lines)
    try:
        broken = parse_session(tmp)
        results.append(_ok("9. Bozuk/bos satirlar cakmadan atlaniyor", True,
                           "{} bozuk satir atlandi, {} istek okundu".format(
                               broken["bad_lines"], len(broken["requests"]))))
    except Exception as exc:
        results.append(_ok("9. Bozuk/bos satirlar cakmadan atlaniyor", False, repr(exc)))
    finally:
        try:
            tmp.unlink()
            os.rmdir(tmpdir)
        except OSError:
            pass

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
                           "yok->None, yaz/oku ayni, sil->None"))
    finally:
        try:
            _tp.unlink()
            os.rmdir(_td)
        except OSError:
            pass

    print("\n-- 22. Takip baslangici --")
    _c22 = json.loads(json.dumps(config))
    _c22["tracking_start_month"] = "2026-08"
    _t_in = parse_ts("2026-08-15T10:00:00.000Z")
    _t_out = parse_ts("2026-07-15T10:00:00.000Z")
    _c22none = json.loads(json.dumps(config))
    _c22none["tracking_start_month"] = None
    results.append(_ok(
        "22. Takip baslangicindan onceki session kapsam disi",
        in_tracking_scope(_t_in, _c22) is True
        and in_tracking_scope(_t_out, _c22) is False
        and in_tracking_scope(_t_out, _c22none) is True,
        "2026-08 esigi: Agu ici -> True, Tem -> False, esik yokken -> True"))

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

    print("\n-- 22b. Aylik kanoniklestirilmesi --")
    import tempfile as _tf22b
    _td22b = _tf22b.mkdtemp(prefix="claude_cost_track_")
    _tp22b = Path(_td22b) / "cost-config.json"
    try:
        main(["--set-tracking-start", "2026-1", "--config", str(_tp22b)])
        with open(str(_tp22b), "r", encoding="utf-8") as _f22b:
            _written22b = json.load(_f22b)
        _canonical = _written22b.get("tracking_start_month")
        _t_aug = parse_ts("2026-08-15T10:00:00.000Z")
        _c22b_scope = json.loads(json.dumps(_written22b))
        results.append(_ok(
            "22b. Sifir-Padded aylar sira saglamasi (2026-1 -> 2026-01 -> Agu dahil)",
            _canonical == "2026-01" and in_tracking_scope(_t_aug, _c22b_scope) is True,
            "gercek CLI uzerinden yazilan deger dolgulu kanonik bicimde ve "
            "Agustos kapsam icinde bulundu"))
    finally:
        try:
            _tp22b.unlink()
            os.rmdir(_td22b)
        except OSError:
            pass

    print("\n-- 23. --tag-project onay kapisi --")
    import tempfile as _tf23
    import contextlib as _ctx23
    import io as _io23
    _td23 = _tf23.mkdtemp(prefix="claude_cost_gate_")
    _tp23_tags = Path(_td23) / "tags.json"
    _tp23_cfg = Path(_td23) / "cost-config.json"
    try:
        with _ctx23.redirect_stdout(_io23.StringIO()):
            _rc23_noyes = main(["--tag-project", "*", "dahil",
                               "--tags", str(_tp23_tags), "--config", str(_tp23_cfg)])
        _no_write23 = (not _tp23_tags.exists()) or (
            not load_tags(_tp23_tags).get("tags"))
        with _ctx23.redirect_stdout(_io23.StringIO()):
            _rc23_yes = main(["--tag-project", "*", "dahil",
                             "--tags", str(_tp23_tags), "--config", str(_tp23_cfg),
                             "--yes"])
        _did_write23 = _tp23_tags.exists() and bool(load_tags(_tp23_tags).get("tags"))
        results.append(_ok(
            "23. --tag-project onay kapisi: --yes yoksa yazma yok, cikis 3",
            _rc23_noyes == 3 and _no_write23 and _rc23_yes == 0 and _did_write23,
            "--yes yok: cikis={}, yazildi={} | --yes var: cikis={}, yazildi={}".format(
                _rc23_noyes, not _no_write23, _rc23_yes, _did_write23)))
    finally:
        try:
            if _tp23_tags.exists():
                _tp23_tags.unlink()
            if _tp23_cfg.exists():
                _tp23_cfg.unlink()
            os.rmdir(_td23)
        except OSError:
            pass

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

    print("\n-- 10. Capraz platform (statik gozden gecirme) --")
    src = Path(__file__).read_text(encoding="utf-8")
    # Selftest'in KENDI govdesi taramadan cikarilir: asagidaki kontrollerin
    # aradigi dizeler bu fonksiyonun icinde birebir gectigi icin, cikarilmazsa
    # her kontrol kendi kendini yakalar ve daima FAIL verir.
    # Sinir aranirken parcali birlestirme kullanilir ("def " + "main("), yoksa
    # bu satirin KENDI literali ilk eslesme olur ve sinir yanlis yere duser.
    src = src[:src.index("def " + "_ok(")] + src[src.index("def " + "main("):]
    # Needle'lar gercek tip-notasyonu kalibina bakar; duz metinde (docstring)
    # gecen ornekler yanlis alarm uretmesin.
    checks = [
        ("Path.home() kullaniliyor", "Path.home()" in src),
        ('encoding="utf-8" ile aciliyor', 'encoding="utf-8"' in src),
        ("Z -> +00:00 donusumu var", "+00:00" in src),
        ("cwd->slug turetmesi YOK", ("def " + "slug_from_cwd") not in src),
        ("3.8-uyumlu: match deyimi yok", "\n    match " not in src),
        ("3.8-uyumlu: yerlesik jenerik notasyon yok",
         not any(n in src for n in (": list[", "-> list[", ": dict[", "-> dict["))),
        ("3.8-uyumlu: 'X | None' tip sozdizimi yok", " | None" not in src),
        ("yol birlestirmede pathlib kullaniliyor (surucu harfi literali yok)",
         ":" + "\\" + "\\" not in src),
    ]
    ok10 = True
    for label, passed in checks:
        ok10 = ok10 and passed
        print("     {} {}".format("ok  " if passed else "FAIL", label))
    results.append(_ok("10. Capraz platform kod incelemesi", ok10))

    print("\n" + "=" * 72)
    passed = sum(1 for r in results if r)
    print("SONUC: {}/{} kontrol gecti".format(passed, len(results)))
    print("=" * 72)
    return 0 if passed == len(results) else 1


# ---------------------------------------------------------------- CLI

def main(argv=None):
    _setup_stdout()
    ap = argparse.ArgumentParser(
        prog="claude_cost.py",
        description="Claude Code session sure + maliyet raporu")
    ap.add_argument("--session", metavar="ID", help="belirli session id")
    ap.add_argument("--month", nargs="?", const="__current__", metavar="YYYY-MM",
                    help="aylik ozet (argumansiz: bu ay)")
    ap.add_argument("--plan", type=float, metavar="TUTAR",
                    help="bu calistirma icin plan tutarini ez")
    ap.add_argument("--set-plan", type=float, metavar="TUTAR",
                    help="config'e kalici plan tutari yaz")
    ap.add_argument("--idle-gap", type=float, metavar="DK",
                    help="ara esigi (dakika) - bu calistirma icin")
    ap.add_argument("--set-idle-gap", type=float, metavar="DK",
                    help="ara esigini (dakika) config'e kalici yaz")
    ap.add_argument("--set-tracking-start", metavar="YYYY-MM",
                    help="takip baslangic ayini config'e kalici yaz")
    ap.add_argument("--tag", nargs=2, metavar=("SESSION-ID", "dahil|haric"),
                    help="tek session'i etiketle")
    ap.add_argument("--untag", metavar="SESSION-ID",
                    help="etiketi kaldir (etiketsize dondur)")
    ap.add_argument("--tag-list", action="store_true",
                    help="session'lari etiketleriyle listele")
    ap.add_argument("--untagged", action="store_true",
                    help="--tag-list ile: yalnizca etiketsizleri goster")
    ap.add_argument("--tag-project", nargs=2, metavar=("DESEN", "dahil|haric"),
                    help="cwd desenine uyan session'lari toplu etiketle")
    ap.add_argument("--yes", action="store_true",
                    help="toplu islemde onay sorma")
    ap.add_argument("--set-baseline", type=float, metavar="TUTAR",
                    help="tabani config'e kalici yaz")
    ap.add_argument("--suggest-baseline", action="store_true",
                    help="taban icin tavsiye goster (hicbir sey yazmaz)")
    ap.add_argument("--currency", metavar="KOD",
                    help="para birimi (--set-plan ile birlikte kalici)")
    ap.add_argument("--label", metavar="AD", help="plan etiketi (Pro, Max, ...)")
    ap.add_argument("--json", action="store_true", help="makine okunur cikti")
    ap.add_argument("--selftest", action="store_true", help="dogrulama modu")
    ap.add_argument("--config", metavar="YOL", help="alternatif config dosyasi")
    ap.add_argument("--tags", metavar="YOL",
                    help="alternatif etiket deposu dosyasi")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)

    config, config_path = load_config(args.config)

    if (args.set_plan is not None or args.set_idle_gap is not None
            or args.set_tracking_start is not None):
        if args.set_plan is not None:
            config["plan"]["amount"] = float(args.set_plan)
        if args.set_idle_gap is not None:
            if args.set_idle_gap <= 0:
                sys.stderr.write("HATA: --set-idle-gap sifirdan buyuk olmali.\n")
                return 2
            config["idle_gap_seconds"] = float(args.set_idle_gap) * 60.0
        if args.set_tracking_start is not None:
            txt = args.set_tracking_start.strip()
            try:
                parsed = datetime.datetime.strptime(txt, "%Y-%m")
            except ValueError:
                sys.stderr.write("HATA: --set-tracking-start YYYY-MM biciminde "
                                 "olmali (ornek 2026-08).\n")
                return 2
            config["tracking_start_month"] = parsed.strftime("%Y-%m")
        if args.currency:
            config["plan"]["currency"] = args.currency
        if args.label:
            config["plan"]["label"] = args.label
        written = save_config(config, config_path)
        print("Config guncellendi: {}".format(written))
        print("  plan     : {} {} ({})".format(
            config["plan"]["amount"], config["plan"]["currency"],
            config["plan"].get("label", "")))
        print("  ara esigi: {}".format(fmt_threshold(config["idle_gap_seconds"])))
        print("  takip baslangici: {}".format(
            config.get("tracking_start_month") or "(tum gecmis)"))
        return 0

    if args.idle_gap is not None:
        if args.idle_gap <= 0:
            sys.stderr.write("HATA: --idle-gap sifirdan buyuk olmali.\n")
            return 2
        config["idle_gap_seconds"] = float(args.idle_gap) * 60.0

    if args.plan is not None:
        config["plan"] = dict(config["plan"])
        config["plan"]["amount"] = float(args.plan)
    if args.currency:
        config["plan"] = dict(config["plan"])
        config["plan"]["currency"] = args.currency

    if args.tag:
        sid, word = args.tag
        included = parse_tag_word(word)
        if included is None:
            sys.stderr.write("HATA: etiket 'dahil' veya 'haric' olmali "
                             "('{}' verildi).\n".format(word))
            return 2
        store = load_tags(args.tags)
        known = set(s["session_id"] for s in collect_sessions(config, tags=store))
        if sid not in known:
            sys.stderr.write("HATA: '{}' kapsamda bir session degil. "
                             "--tag-list ile bakin.\n".format(sid))
            return 2
        set_tag(store, sid, included)
        written = save_tags(store, args.tags)
        print("Etiketlendi: {} -> {}".format(sid, TAG_LABELS[included]))
        print("  {}".format(written))
        return 0

    if args.untag:
        store = load_tags(args.tags)
        if get_tag(store, args.untag) is None:
            print("Zaten etiketsiz: {}".format(args.untag))
            return 0
        remove_tag(store, args.untag)
        save_tags(store, args.tags)
        print("Etiket kaldirildi: {} -> etiketsiz".format(args.untag))
        return 0

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

    if args.tag_project:
        desen, word = args.tag_project
        included = parse_tag_word(word)
        if included is None:
            sys.stderr.write("HATA: etiket 'dahil' veya 'haric' olmali "
                             "('{}' verildi).\n".format(word))
            return 2
        # fnmatch buyuk/kucuk harf duyarsiz karsilastirilir: Windows yollari icin sart.
        d = desen.lower()
        display_store = load_tags(args.tags)
        hedef = [s for s in collect_sessions(config, tags=display_store)
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
        store = load_tags(args.tags)
        for s in hedef:
            set_tag(store, s["session_id"], included)
        save_tags(store, args.tags)
        print("{} session etiketlendi.".format(len(hedef)))
        return 0

    if args.tag_list:
        print(render_tag_list(collect_sessions(config, tags=load_tags(args.tags)),
                              only_untagged=args.untagged))
        return 0

    if args.selftest:
        return selftest(config, config_path)

    if not PROJECTS_DIR.exists():
        sys.stderr.write("HATA: {} bulunamadi. Claude Code transcript dizini yok.\n"
                         .format(PROJECTS_DIR))
        return 2

    if args.month:
        month = (datetime.datetime.now().strftime("%Y-%m")
                 if args.month == "__current__" else args.month)
        report = build_month_report(month, config, config_path)
    else:
        path, how = find_session_file(args.session)
        if path is None:
            sys.stderr.write("HATA: {} altinda hic transcript (.jsonl) yok.\n"
                             .format(PROJECTS_DIR))
            return 2
        report = build_session_report(path, config, config_path)
        report["session"]["found_by"] = how

    print(render_json(report) if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
