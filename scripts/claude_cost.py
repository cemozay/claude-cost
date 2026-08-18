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
import math
import os
import sys
import socket
import datetime
import fnmatch

from pathlib import Path

__version__ = "1.1.0"

# ---------------------------------------------------------------- sabitler

CONFIG_PATH = Path.home() / ".claude" / "cost-config.json"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
TAGS_PATH = Path.home() / ".claude" / "cost-tags.json"
IMPORTS_DIR = Path.home() / ".claude" / "cost-imports"
EXPORT_VERSION = 1

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

    # Deger dogrulamasi: yalnizca gercek bool (True/False) kabul edilir.
    # Elle duzenlenmis dosya 1/0/"true"/null gibi degerler tasiyabilir.
    # `isinstance(1, bool)` False, ama Python'da `1 == True` oldugu icin bir
    # dict anahtari olarak True ile 1 CAKISIR; sessizce kabul edilirse uc
    # durumlu mantik (dahil/haric/etiketsiz) bozulur ve rapor ya yanlis
    # etiketle ("dahil" gorunur) ya da coker. Gecersiz deger asla sessizce
    # coz(dur)ulmez: acikca stderr'e uyari yazilir ve o oturum etiketsiz
    # sayilir.
    gecerli_tags = {}
    for sid, val in data["tags"].items():
        if val is True or val is False:
            gecerli_tags[sid] = val
        else:
            sys.stderr.write(
                "UYARI: etiket dosyasinda gecersiz deger, atlaniyor "
                "(etiketsiz sayilacak): session={} deger={!r}\n".format(sid, val))
    data = dict(data)
    data["tags"] = gecerli_tags
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


def collect_sessions(config, tags=None, quiet=True, include_imports=True):
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
    out.sort(key=lambda s: s["start"])
    return out


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


# ---------------------------------------------------------------- 7. plan payi

class BaselineNotSetError(Exception):
    """Taban set edilmemis. Rapor sayi uydurmaz, durur."""


def fixed_plan_cost(session_cost, config):
    """Sabit oranli plan maliyeti.

    session_API_karsiligi / TABAN x plan_tutari

    Payda ay toplami DEGIL dondurulmus tabandir; bu yuzden rakam session
    bitiminde sabitlenir ve sonradan acilan oturumlardan etkilenmez.
    """
    raw = config.get("baseline_monthly_api_cost")
    # Elle duzenlenmis config her seyi icerebilir. json modulu Infinity/NaN'i
    # kabul ettigi icin naif bir ">0" kontrolu Infinity'yi gecirir ve sonuc
    # sessizce $0.00 cikar. Sayi olmayan / sonlu olmayan / pozitif olmayan
    # her deger acikca reddedilir.
    try:
        baseline = float(raw)
    except (TypeError, ValueError):
        baseline = None
    if baseline is None or not math.isfinite(baseline) or baseline <= 0:
        raise BaselineNotSetError(
            "Taban set edilmemis ya da gecersiz (deger: {!r}). Once "
            "'--suggest-baseline' ile bakin, sonra '--set-baseline <tutar>' "
            "ile yazin.".format(raw))
    plan_cfg = config.get("plan") or {}
    plan_amount = float(plan_cfg.get("amount", 0.0))
    ratio = float(session_cost) / baseline
    return {"ratio": ratio, "amount": ratio * plan_amount,
            "baseline": baseline}


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
        L.append("Etiket:    {}".format(TAG_LABELS[report.get("tag")]))
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
        L.append("")

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
        elif not report.get("fixed_cost"):
            # Buraya normalde tag True VE baseline_error yokken girilir; o
            # durumda fixed_cost dolu olmali. Eksikse rapor kurulumunda bir
            # tutarsizlik var demektir. Coken bir dereferans yerine acik bir
            # mesaj: yanlis rakam basmaktansa hicbir rakam basmamak yeglenir.
            L.append("  HATA: sabit plan maliyeti hesaplanamadi (beklenmeyen "
                     "durum: fixed_cost eksik). Rakam basilmadi.")
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
    else:
        m = report["month"]
        mk = m["month"]
        y, mo = mk.split("-")
        etiket = "  (ay ici, gecici)" if is_current_month(mk) else ""
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

def build_session_report(path, config, config_path, tags=None):
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
        "baseline_source": config.get("baseline_source"),
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

    if tags is None:
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

    print("\n-- 7. Aylik ozet - taban kablolamasi --")
    # month_totals artik plan_share() DEGIL fixed_plan_cost() cagiriyor (ay
    # toplami $20'ye civilenmiyor). Beklenen deger BAGIMSIZ hesaplanip
    # (dahil_cost / taban x plan) month_totals'un urettigi utilization/plan_cost
    # ile karsilastiriliyor -- fonksiyonu tekrar cagirip kendi kendini
    # dogrulayan sahte bir kontrole donusmesin diye. En az bir dahil-etiketli
    # session'in kesin bulundugu icinde bulunulan ay kullanilir (d["start"]'in
    # ait oldugu ay etiketsiz/haric agirlikli olabilir, o zaman dahil_cost 0
    # kalir ve asagidaki oran/tutar karsilastirmalari sessizce anlamsizlasir).
    mk = datetime.datetime.now().strftime("%Y-%m")
    _c7 = json.loads(json.dumps(config))
    _c7["baseline_monthly_api_cost"] = 1000.0
    _c7["plan"] = {"amount": 20.0, "currency": "USD", "label": "Pro"}
    mt = month_totals(mk, _c7)
    _toplam_ayri7 = abs((mt["dahil_cost"] + mt["haric_cost"] + mt["etiketsiz_cost"])
                        - mt["tum_cost"]) < 0.01
    _beklenen_oran7 = mt["dahil_cost"] / 1000.0
    _beklenen_tutar7 = _beklenen_oran7 * 20.0
    results.append(_ok(
        "7. Aylik dahil toplami tabana bolunup dogru kullanim orani/plan "
        "maliyeti uretiyor",
        mt["dahil_cost"] > 0  # sifirsa oran/tutar karsilastirmalari 0==0 olur, anlamsizlasir
        and mt["baseline_error"] is None
        and abs(mt["utilization"] - _beklenen_oran7) < 1e-9
        and abs(mt["plan_cost"] - _beklenen_tutar7) < 1e-9
        and _toplam_ayri7,
        "{} dahil session, dahil_cost {} -> kullanim %{:.1f}".format(
            len(mt["dahil"]), fmt_money(mt["dahil_cost"]), mt["utilization"] * 100.0)))

    print("\n-- 8. Plan override (aylik) --")
    _c8 = json.loads(json.dumps(_c7))
    _c8["plan"]["amount"] = _c7["plan"]["amount"] * 5
    mt5 = month_totals(mk, _c8)
    results.append(_ok(
        "8. Plan 5x iken kullanim orani sabit, plan maliyeti 5x",
        mt["baseline_error"] is None and mt5["baseline_error"] is None
        and abs(mt["utilization"] - mt5["utilization"]) < 1e-9
        and abs(mt5["plan_cost"] - mt["plan_cost"] * 5) < 1e-9,
        "oran %{:.1f} sabit, tutar {} -> {}".format(
            mt["utilization"] * 100.0, fmt_money(mt["plan_cost"]),
            fmt_money(mt5["plan_cost"]))))

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

    print('\n-- 21b. Gecersiz taban --')
    # json modulu Infinity/NaN kabul eder; elle duzenlenmis config bunlari
    # tasiyabilir. Naif ">0" kontrolu Infinity'yi gecirir ve sonuc sessizce
    # $0.00 cikar. Hepsi acikca reddedilmeli.
    _bad = [None, 0, -5, "abc", float("inf"), float("-inf"), float("nan")]
    _reddedilen = []
    for _v in _bad:
        _c = json.loads(json.dumps(config))
        _c["baseline_monthly_api_cost"] = None
        _c2 = dict(_c)
        _c2["baseline_monthly_api_cost"] = _v
        try:
            fixed_plan_cost(100.0, _c2)
        except BaselineNotSetError:
            _reddedilen.append(_v)
        except Exception as _e:
            _reddedilen.append("HAM-ISTISNA:{}".format(type(_e).__name__))
    _c_ok = json.loads(json.dumps(config))
    _c_ok["baseline_monthly_api_cost"] = 1000.0
    _c_ok["plan"] = {"amount": 20.0, "currency": "USD", "label": "Pro"}
    _gecerli = abs(fixed_plan_cost(50.0, _c_ok)["amount"] - 1.0) < 1e-9
    results.append(_ok(
        "21b. Gecersiz taban (Infinity/NaN/metin/0/negatif) reddediliyor",
        len(_reddedilen) == len(_bad)
        and all(not isinstance(x, str) or not x.startswith("HAM-ISTISNA")
                for x in _reddedilen)
        and _gecerli,
        "{}/{} deger reddedildi, gecerli taban calisiyor".format(
            len(_reddedilen), len(_bad))))

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

    print("\n-- 19b. render_text entegrasyonu (dahil/haric/etiketsiz/tabansiz) --")
    # 19 formulu (fixed_plan_cost) dogrudan cagirarak dogruluyor; wiring'i,
    # yani build_session_report -> render_text yolunu, dogrulamiyor. Burada
    # gercek yol suruluyor ve tek guvenilir isaret olan "[SABIT]" etiketinin
    # varligina/yoklugna bakiliyor.
    _c19b = json.loads(json.dumps(config))
    _c19b["baseline_monthly_api_cost"] = 1000.0
    _c19b["plan"] = {"amount": 20.0, "currency": "USD", "label": "Pro"}
    _path19b = biggest
    _probe19b = build_session_report(_path19b, _c19b, config_path,
                                     tags={"version": 1, "tags": {}})
    _sid19b = _probe19b["session"]["session_id"]

    def _store19b(value):
        store = {"version": 1, "tags": {}}
        if value is not None:
            store["tags"][_sid19b] = value
        return store

    _txt19b_dahil = render_text(build_session_report(
        _path19b, _c19b, config_path, tags=_store19b(True)))
    _r19b_dahil = "[SABIT]" in _txt19b_dahil

    # [SABIT] etiketi render_text'in format dizesinde SABIT bir dize: dogru
    # dala girildiginde kosulsuz basilir, basilan RAKAMIN dogrulugunu KANITLAMAZ.
    # build_session_report -> fixed_plan_cost hattina yanlis deger sizsa (mesela
    # session yerine ay toplami gecirilse) [SABIT] yine basilir ve bu kontrol
    # yine PASS derdi. Bu yuzden beklenen tutar burada BAGIMSIZ (fixed_plan_cost
    # cagirmadan) hesaplanip render_text'in urettigi TAM satirla karsilastirilir.
    _session_cost19b = _probe19b["total_cost"]
    _baseline19b = 1000.0
    _plan_amt19b = 20.0
    _expected_amount19b = _session_cost19b / _baseline19b * _plan_amt19b
    _expected_line19b = "  Bu session     : {} / {} x {}  =  {}   [SABIT]".format(
        fmt_money(_session_cost19b, "USD"),
        fmt_money(_baseline19b, "USD"),
        fmt_money(_plan_amt19b, "USD"),
        fmt_money(_expected_amount19b, "USD"))
    _r19b_dahil_deger = _expected_line19b in _txt19b_dahil

    _txt19b_haric = render_text(build_session_report(
        _path19b, _c19b, config_path, tags=_store19b(False)))
    _r19b_haric = "[SABIT]" not in _txt19b_haric and "HARIC" in _txt19b_haric

    _txt19b_etiketsiz = render_text(build_session_report(
        _path19b, _c19b, config_path, tags=_store19b(None)))
    _r19b_etiketsiz = "[SABIT]" not in _txt19b_etiketsiz and "--tag" in _txt19b_etiketsiz

    _c19b_tabansiz = json.loads(json.dumps(_c19b))
    _c19b_tabansiz["baseline_monthly_api_cost"] = None
    _txt19b_tabansiz = render_text(build_session_report(
        _path19b, _c19b_tabansiz, config_path, tags=_store19b(True)))
    _r19b_tabansiz = "[SABIT]" not in _txt19b_tabansiz

    results.append(_ok(
        "19b. render_text dort durumu dogru basiyor/basmiyor",
        _r19b_dahil and _r19b_dahil_deger and _r19b_haric and _r19b_etiketsiz
        and _r19b_tabansiz,
        "dahil->[SABIT] var + rakam dogru ({}); haric/etiketsiz/tabansiz"
        "->[SABIT] yok".format(fmt_money(_expected_amount19b, "USD"))))

    print("\n-- 19c. Bozuk etiket degeri (guvensiz depo) --")
    # Elle duzenlenmis cost-tags.json {"tags": {sid: 1}} gibi bir deger
    # tasiyabilir (bool degil, int 1). load_tags bunu REDDETMELI: uyari
    # yazip kaydi dusurmeli, boylece session etiketsiz okunur. Reddedilmezse
    # `report["tag"] is True` False doner (1 is not True), rapor "dahil"
    # gorunur (TAG_LABELS[1] == TAG_LABELS[True], cunku 1 == True) ama
    # fixed_cost hic hesaplanmaz -> render_text'te coken bir dereferans.
    import tempfile as _tf19c
    import io as _io19c
    import contextlib as _ctx19c
    _td19c = _tf19c.mkdtemp(prefix="claude_cost_badtag_")
    _tp19c = Path(_td19c) / "tags.json"
    try:
        _sid19c = _sid19b
        with open(str(_tp19c), "w", encoding="utf-8") as _f19c:
            json.dump({"version": 1, "tags": {_sid19c: 1}}, _f19c)

        _stderr19c = _io19c.StringIO()
        with _ctx19c.redirect_stderr(_stderr19c):
            _store19c = load_tags(_tp19c)
        _warn19c = ("UYARI" in _stderr19c.getvalue()
                    and _sid19c in _stderr19c.getvalue())
        _dropped19c = get_tag(_store19c, _sid19c) is None

        _crash19c = False
        try:
            _report19c = build_session_report(_path19b, _c19b, config_path,
                                              tags=_store19c)
            _txt19c = render_text(_report19c)
        except Exception as _e19c:
            _crash19c = True
            _txt19c = "COKTU: {!r}".format(_e19c)
        _no_crash19c = not _crash19c
        _etiketsiz19c = "Etiket:    etiketsiz" in _txt19c

        results.append(_ok(
            "19c. Bozuk etiket degeri (1) uyariyla dusuruluyor, etiketsiz "
            "okunuyor, cokmuyor",
            _warn19c and _dropped19c and _no_crash19c and _etiketsiz19c,
            "uyari-yazildi={}, depodan-dustu={}, coktu={}, "
            "etiket-satiri-etiketsiz={}".format(
                _warn19c, _dropped19c, _crash19c, _etiketsiz19c)))
    finally:
        try:
            _tp19c.unlink()
            os.rmdir(_td19c)
        except OSError:
            pass

    print("\n-- 19d. --tags/--config main() uctan uca --")
    # Onceki bir duzeltme build_session_report'a tags= parametresini gecirdi
    # ve main() bunu load_tags(args.tags) ile besledi (--tags eskiden session
    # raporlarinda YOK sayiliyordu). Bu duzeltmenin dogrudan bir testi yoktu:
    # 19b build_session_report'u main() uzerinden DEGIL, bellek-ici sozlukle
    # cagiriyor. `report = build_session_report(path, config, config_path,
    # tags=load_tags(args.tags))` satirindaki `tags=load_tags(args.tags)`
    # geri alinsa (gercek depo kullanilsa) bu test YAKALAMALI.
    import tempfile as _tf19d
    import io as _io19d
    import contextlib as _ctx19d
    _td19d = _tf19d.mkdtemp(prefix="claude_cost_e2e_")
    _tp19d_tags = Path(_td19d) / "tags.json"
    _tp19d_cfg = Path(_td19d) / "cost-config.json"
    try:
        _sid19d = Path(_path19b).stem
        save_tags(set_tag({"version": 1, "tags": {}}, _sid19d, False), _tp19d_tags)
        _out19d = _io19d.StringIO()
        with _ctx19d.redirect_stdout(_out19d):
            _rc19d = main(["--session", _sid19d,
                          "--tags", str(_tp19d_tags),
                          "--config", str(_tp19d_cfg)])
        _txt19d = _out19d.getvalue()
        _ok19d = (_rc19d == 0 and "[SABIT]" not in _txt19d
                 and "Bu session HARIC tutulmus" in _txt19d)
        results.append(_ok(
            "19d. main() --tags gecici depoyu okuyor (gercek depoyu degil)",
            _ok19d,
            "cikis={}, [SABIT]-yok={}, HARIC-var={}".format(
                _rc19d, "[SABIT]" not in _txt19d,
                "Bu session HARIC tutulmus" in _txt19d)))
    finally:
        try:
            if _tp19d_tags.exists():
                _tp19d_tags.unlink()
            if _tp19d_cfg.exists():
                _tp19d_cfg.unlink()
            os.rmdir(_td19d)
        except OSError:
            pass

    print('\n-- 19e. Savunmaci dal: sanitize edilmemis etiket --')
    # load_tags() bozuk degeri suzuyor (19c), ama build_session_report(tags=...)
    # HAM sozluk de kabul ediyor. Boyle bir cagirci 1 gecirirse tag ne True ne
    # False ne None olur; render_text'in son dali fixed_cost'u dereference
    # etmeye calisip cokerdi. Savunmaci dal bunu engelliyor -- ve bu kontrol
    # olmadan o dalin silindigi FARK EDILMEZDI (review deneyle gosterdi).
    _ham19e = {"version": 1, "tags": {_sid19b: 1}}
    _coktu19e = False
    _txt19e = ""
    try:
        _txt19e = render_text(build_session_report(
            _path19b, _c19b, config_path, tags=_ham19e))
    except Exception as _e19e:
        _coktu19e = True
        _txt19e = "COKME: {}".format(type(_e19e).__name__)
    results.append(_ok(
        "19e. Sanitize edilmemis etiket cokmeye degil acik mesaja donuyor",
        (not _coktu19e) and "[SABIT]" not in _txt19e
        and "fixed_cost eksik" in _txt19e,
        "coktu={} | [SABIT]-yok={} | acik-mesaj={}".format(
            _coktu19e, "[SABIT]" not in _txt19e,
            "fixed_cost eksik" in _txt19e)))

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

    print("\n-- 18b. export_sessions include_imports=False korumasi --")
    import tempfile as _tf18b
    _gd = Path(_tf18b.mkdtemp(prefix="claude_cost_guard_"))
    try:
        # IMPORTS_DIR'a sahte bir import dosyasi koyup gecici olarak
        # IMPORTS_DIR'i buraya yonlendiriyoruz. export_sessions eger
        # include_imports=False kullanmiyorsa bu sahte session'i kendi
        # ciktisina sizdirir - guardin kaldirilmasini yakalayan tek kontrol budur.
        _fake_sid = "GUARD-TEST-SESSION-ID-DOES-NOT-EXIST"
        _now18b = datetime.datetime.now(datetime.timezone.utc)
        _fake_payload = {
            "version": EXPORT_VERSION, "machine": "sahte-makine",
            "exported_at": _now18b.isoformat(),
            "sessions": [{
                "session_id": _fake_sid, "start": _now18b.isoformat(),
                "end": (_now18b + datetime.timedelta(minutes=10)).isoformat(),
                "active_seconds": 600.0, "wall_seconds": 600.0,
                "cwd": "/fake", "gitBranch": None, "title": "fake",
                "request_count": 1, "tokens": {}, "cost": 999.0,
                "unknown_models": [],
            }],
        }
        with open(str(_gd / "sahte.json"), "w", encoding="utf-8") as _fh18b:
            json.dump(_fake_payload, _fh18b)
        global IMPORTS_DIR
        _orig_imports_dir = IMPORTS_DIR
        IMPORTS_DIR = _gd
        try:
            _out18b = _gd / "cikti.json"
            export_sessions(config, _out18b, machine="guard-test")
            with open(str(_out18b), "r", encoding="utf-8") as _fh18b2:
                _guard_payload = json.load(_fh18b2)
            _guard_ids = set(e["session_id"] for e in _guard_payload["sessions"])
            results.append(_ok(
                "18b. export_sessions import edilenleri yeniden yaymiyor",
                _fake_sid not in _guard_ids,
                "sahte import session'i export ciktisinda {}".format(
                    "bulundu (HATA)" if _fake_sid in _guard_ids else "yok (dogru)")))
        finally:
            IMPORTS_DIR = _orig_imports_dir
    finally:
        try:
            for _f in _gd.glob("*"):
                _f.unlink()
            os.rmdir(str(_gd))
        except OSError:
            pass

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
    ap.add_argument("--export", metavar="DOSYA",
                    help="bu makinenin session ozetini yaz")
    ap.add_argument("--machine", metavar="AD",
                    help="export'a yazilacak makine adi (varsayilan: hostname)")
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

    if args.export:
        written = export_sessions(config, args.export, args.machine)
        print("Export edildi: {}".format(written))
        print("  Bu dosyayi ana makinede {} klasorune kopyalayin.".format(IMPORTS_DIR))
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
        report = build_session_report(path, config, config_path,
                                      tags=load_tags(args.tags))
        report["session"]["found_by"] = how

    print(render_json(report) if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
