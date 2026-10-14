#!/usr/bin/env python3
"""
PROJECT ARGUS — Market Intel & Trend Hunter Engine (single-file version)
PRD v1.0.0 diringkas jadi 1 script agar gampang dijadikan Skill OpenCode.

Tanpa API key tetap jalan (stdlib-first). Dependensi opsional dipakai kalau ada:
  pip install pytrends duckduckgo-search trafilatura beautifulsoup4 playwright tavily-python
  playwright install chromium  # hanya jika butuh render JS berat

Contoh pakai (setara PRD):
  python research.py trend --query "SaaS HRIS Indonesia" --source google,reddit --timeframe 7d
  python research.py intel --industry "Fintech Lending" --topic "regulation,funding"
  python research.py spy --url "https://competitor.com/pricing" --save-snapshot
  python research.py spy --diff competitor_com_pricing
  python research.py brief --query "SaaS HRIS Indonesia" --industry "SaaS HRIS" --url "https://competitor.com/pricing"

Untuk Skill: cukup panggil script ini via bash, lalu tempel stdout-nya ke meeting note.
Output selalu format Executive Briefing (BLUF) + tabel/data mentah bila diminta --json.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import html as ihtml
import json
import os
import random
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

DATA_DIR = Path(os.getenv("ARGUS_DATA_DIR", str(Path(__file__).resolve().parent)))
SNAP_DIR = DATA_DIR / "snapshots"
DB_PATH = DATA_DIR / "argus.db"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

POS_WORDS = set("""
bagus baik hebat mantap puas suka love great awesome excellent amazing helpful
mudah murah cepat untung naik tumbuh inovatif recommended recommended sukses
efisien transparan aman nyaman puas worth".split()""".replace('"', '').split())
NEG_WORDS = set("""
buruk jelek mahal lemot lelet error gagal kecewa scam penipu ribet susah lambat
turun rugi phk bocor bug mahal complaint buruk spam spamming nyesel waspada
bahaya denda blokir tuntut masalah bad poor worst hate disappointed slow expensive
broken useless scam fraud lawsuit down outage""".split())

TOPIC_KEYWORDS = {
    "regulation": ["regulasi", "regulation", "aturan", "ojk", "bi ", "pemerintah", "kebijakan", "undang-undang", "uu ", "peraturan", "compliance", "izin", "lisensi"],
    "funding": ["pendanaan", "funding", "funded", "investasi", "series a", "series b", "seed", "unicorn", "ipo", "venture", "modal"],
    "merger": ["merger", "akuisisi", "acquisition", "konsolidasi"],
    "lawsuit": ["tuntutan", "hukum", "lawsuit", "gugatan", "denda", "sanksi", "pidana", "pengadilan"],
    "policy": ["kebijakan", "policy", "subsidi", "pajak", "tarif"],
}
CLICKBAIT_BLOCK = ["slot gacor", "togel", "judi", "bokep", "giveaway 100%", "klik di sini untuk kaya", "obat kuat"]


def slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"https?://", "", s)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:60] or "snapshot"


def pick_ua() -> str:
    return random.choice(USER_AGENTS)


def http_get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": pick_ua(), "Accept-Language": "id,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        charset = r.headers.get_content_charset() or "utf-8"
        try:
            return raw.decode(charset, errors="replace")
        except Exception:
            return raw.decode("utf-8", errors="replace")


def http_get_json(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": pick_ua(), "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def clean_html(html_text: str, max_chars: int = 12000) -> str:
    """Boilerplate removal token-efficient: buang nav/footer/script/style, hemat ~70% token."""
    try:
        import trafilatura  # opsional, terbaik bila ada
        t = trafilatura.extract(html_text, include_comments=False, include_tables=True)
        if t and len(t.strip()) > 200:
            return collapse(t[:max_chars])
    except Exception:
        pass
    try:
        from bs4 import BeautifulSoup  # opsional kedua
        soup = BeautifulSoup(html_text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe", "form"]):
            tag.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        return collapse(main.get_text(separator="\n")[:max_chars])
    except Exception:
        pass
    # Fallback stdlib murni
    txt = re.sub(r"(?is)<(script|style|nav|footer|header|aside|noscript|iframe)[^>]*>.*?</\1>", " ", html_text)
    txt = re.sub(r"(?s)<[^>]+>", " ", txt)
    txt = ihtml.unescape(txt)
    return collapse(txt[:max_chars])


def collapse(t: str) -> str:
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t.strip()


def sentiment_score(text: str) -> float:
    toks = re.findall(r"[a-zA-Z]+", text.lower())
    if not toks:
        return 0.0
    p = sum(1 for w in toks if w in POS_WORDS)
    n = sum(1 for w in toks if w in NEG_WORDS)
    if p == 0 and n == 0:
        return 0.0
    return round((p - n) / max(1, (p + n)), 3)


# ---------- Storage (JSON + SQLite, lokal) ----------

def db_init():
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS snapshots(
      slug TEXT PRIMARY KEY, url TEXT, sha TEXT, chars INTEGER, created_at TEXT, updated_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS trend_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT, growth_pct REAL,
      sentiment REAL, created_at TEXT)""")
    con.commit()
    con.close()


def snapshot_path(slug: str) -> Path:
    return SNAP_DIR / f"{slug}.txt"


def save_snapshot(slug: str, url: str, text: str):
    db_init()
    snapshot_path(slug).write_text(text, encoding="utf-8")
    sha = hashlib.sha256(text.encode()).hexdigest()[:16]
    now = datetime.now().isoformat(timespec="seconds")
    con = sqlite3.connect(DB_PATH)
    con.execute("""INSERT INTO snapshots(slug,url,sha,chars,created_at,updated_at)
      VALUES(?,?,?,?,?,?)
      ON CONFLICT(slug) DO UPDATE SET url=excluded.url, sha=excluded.sha,
      chars=excluded.chars, updated_at=excluded.updated_at""",
                (slug, url, sha, len(text), now, now))
    con.commit()
    con.close()
    return sha


def load_snapshot(slug: str) -> str | None:
    p = snapshot_path(slug)
    return p.read_text(encoding="utf-8") if p.exists() else None


def diff_texts(old: str, new: str, n_context: int = 2) -> dict:
    a, b = old.splitlines(), new.splitlines()
    sm = difflib.SequenceMatcher(None, a, b)
    added = [l for tag, *_ , in [(o.tag,) for o in []] for l in []]  # placeholder, dihitung di bawah
    uni = list(difflib.unified_diff(a, b, lineterm="", n=n_context))
    added_lines = [l[1:] for l in uni if l.startswith("+") and not l.startswith("+++")]
    removed_lines = [l[1:] for l in uni if l.startswith("-") and not l.startswith("---")]
    changed = len(added_lines) + len(removed_lines) > 0
    # Highlight harga/fitur (heuristic PRD)
    hl = []
    pat = re.compile(r"(rp ?[\d.,]+|\$\s?[\d.,]+|\d+\s?%|gratis|free|pro|basic|premium|unlimited|export|ai|chatbot|fitur|harga|paket|plan)", re.I)
    for l in added_lines[:20]:
        if pat.search(l):
            hl.append(f"+ {l.strip()[:200]}")
    for l in removed_lines[:20]:
        if pat.search(l):
            hl.append(f"- {l.strip()[:200]}")
    return {
        "changed": changed,
        "ratio": round(sm.ratio(), 4),
        "added": len(added_lines),
        "removed": len(removed_lines),
        "highlights": hl[:10],
        "unified": "\n".join(uni[:120]),
    }


# ---------- Pilar 1: Trends & Social ----------

def google_trend(query: str, timeframe: str = "7d") -> dict:
    """Coba pytrends, gagal -> fallback heuristik (tetap balikan struktur sama)."""
    tf_map = {"24h": "now 1-d", "7d": "now 7-d", "30d": "today 1-m", "90d": "today 3-m"}
    tf = tf_map.get(timeframe, "now 7-d")
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="id-ID", tz=420, timeout=(5, 8))
        pt.build_payload([query], timeframe=tf, geo="ID")
        df = pt.interest_over_time()
        rel = pt.related_queries()
        if df is not None and not df.empty and query in df.columns:
            vals = df[query].tolist()
            first, last = max(1, vals[0]), max(1, vals[-1])
            growth = round((last - first) / first * 100, 1)
            rq = []
            try:
                rq = rel[query]["top"]["query"].head(5).tolist() if rel and query in rel else []
            except Exception:
                pass
            return {"source": "pytrends", "growth_pct": growth, "avg_interest": round(sum(vals) / len(vals), 1),
                    "related": rq, "timeframe": timeframe}
    except Exception as e:
        return {"source": f"fallback ({type(e).__name__})", "growth_pct": None, "avg_interest": None,
                "related": [], "timeframe": timeframe,
                "note": "pytrends tidak tersedia/gagal — pakai sinyal Reddit+news sebagai proksi."}
    return {"source": "fallback", "growth_pct": None, "avg_interest": None, "related": [], "timeframe": timeframe}


def reddit_sentiment(query: str, limit: int = 40, timeframe: str = "7d") -> dict:
    """Reddit JSON publik tanpa API key. Gagal (rate-limit/offline) -> struktur kosong yang aman."""
    t_map = {"24h": "day", "7d": "week", "30d": "month", "90d": "year"}
    t = t_map.get(timeframe, "week")
    url = f"https://www.reddit.com/search.json?q={urllib.parse.quote(query)}&sort=top&limit={min(limit,100)}&t={t}"
    posts: list[dict] = []
    try:
        data = http_get_json(url, timeout=10)
        for c in data.get("data", {}).get("children", []):
            d = c.get("data", {})
            title = d.get("title", "") or ""
            body = (d.get("selftext", "") or "")[:800]
            posts.append({"title": title, "text": body, "score": d.get("score", 0),
                          "url": "https://reddit.com" + str(d.get("permalink", ""))})
    except Exception as e:
        return {"count": 0, "pos": 0, "neu": 0, "neg": 0, "score": 0.0,
                "top_pains": [], "samples": [], "note": f"reddit fetch gagal: {type(e).__name__} (rate-limit/offline?)."}
    pos = neu = neg = 0
    scored = []
    for p in posts:
        s = sentiment_score(p["title"] + " " + p["text"])
        p["sentiment"] = s
        scored.append(p)
        if s > 0.2:
            pos += 1
        elif s < -0.2:
            neg += 1
        else:
            neu += 1
    avg = round(sum(p["sentiment"] for p in scored) / max(1, len(scored)), 3)
    pains = [p["title"] for p in sorted(scored, key=lambda x: x["sentiment"])[:3] if p["sentiment"] < 0.1]
    return {"count": len(scored), "pos": pos, "neu": neu, "neg": neg, "score": avg,
            "top_pains": pains, "samples": scored[:5]}


def do_trend(query: str, source: str = "google,reddit", timeframe: str = "7d") -> dict:
    srcs = {s.strip().lower() for s in source.split(",")}
    out: dict = {"query": query, "timeframe": timeframe}
    if "google" in srcs:
        out["trend"] = google_trend(query, timeframe)
    if "reddit" in srcs:
        out["sentiment"] = reddit_sentiment(query, timeframe=timeframe)
    return out


# ---------- Pilar 2: Live Intel ----------

def ddg_search(query: str, max_results: int = 10, timelimit: str = "w") -> list[dict]:
    """Prioritas: duckduckgo-search lib -> Tavily (jika key) -> fallback HTML lite (stdlib)."""
    # 1) duckduckgo-search
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddg:
            rows = list(ddg.text(query, max_results=max_results, timelimit=timelimit))
        return [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")} for r in rows]
    except Exception:
        pass
    # 2) Tavily bila ada key
    if os.getenv("TAVILY_API_KEY"):
        try:
            from tavily import TavilyClient
            tv = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
            res = tv.search(query, max_results=max_results, time_range=timelimit, search_depth="basic")
            return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")[:300]}
                    for r in res.get("results", [])]
        except Exception:
            pass
    # 3) Fallback stdlib: DuckDuckGo lite HTML
    try:
        q = urllib.parse.urlencode({"q": query})
        html_text = http_get(f"https://html.duckduckgo.com/html/?{q}", timeout=10)
        links = re.findall(r'(?is)<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?(?is:<a[^>]+class="result__snippet"[^>]*>(.*?)</a>)?', html_text)
        out = []
        for href, title, _snip in links[:max_results]:
            m = re.search(r"uddg=([^&]+)", href)
            url = urllib.parse.unquote(m.group(1)) if m else ihtml.unescape(href)
            title = re.sub(r"<[^>]+>", "", ihtml.unescape(title)).strip()
            out.append({"title": title, "url": url, "snippet": ""})
        return out
    except Exception:
        return []


def filter_intel(articles: list[dict], topics: list[str]) -> list[dict]:
    wanted: list[str] = []
    for t in topics:
        wanted += TOPIC_KEYWORDS.get(t.strip().lower(), [t])
    kept = []
    for a in articles:
        blob = f"{a.get('title','')} {a.get('snippet','')}".lower()
        if any(c in blob for c in CLICKBAIT_BLOCK):
            continue
        hits = [k for k in wanted if k.lower() in blob]
        if wanted and not hits:
            continue
        a["topic_hits"] = hits[:4]
        a["threat_or_opportunity"] = "ancam" if any(k in blob for k in ["tuntut", "denda", "sanksi", "blokir", "larang", "lawsuit", "denda", "phk", "bocor"]) else "peluang"
        kept.append(a)
    return kept[:8]


def do_intel(industry: str, topic: str = "regulation,funding", max_results: int = 10) -> dict:
    topics = [t.strip() for t in topic.split(",") if t.strip()]
    q = f"{industry} ({' OR '.join(topics)})"
    raw = ddg_search(q, max_results=max_results, timelimit="w")
    # perkaya snippet bila kosong: fetch 1 halaman cepat (best-effort, timeout pendek)
    for a in raw[:5]:
        if not a.get("snippet") and a.get("url", "").startswith("http"):
            try:
                a["snippet"] = clean_html(http_get(a["url"], timeout=8), max_chars=400)[:300]
            except Exception:
                pass
    return {"industry": industry, "topics": topics, "count_raw": len(raw),
            "articles": filter_intel(raw, topics)}


# ---------- Pilar 3: Spy & Scraper ----------

def scrape_url(url: str, render_js: bool = False) -> str:
    if render_js:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True)
                pg = b.new_page(user_agent=pick_ua())
                pg.goto(url, wait_until="networkidle", timeout=30000)
                for _ in range(4):  # auto-scroll lazy-load
                    pg.mouse.wheel(0, 2500)
                    pg.wait_for_timeout(600)
                html_text = pg.content()
                b.close()
            return clean_html(html_text)
        except Exception as e:
            print(f"[spy] playwright gagal ({type(e).__name__}), fallback urllib.", file=sys.stderr)
    return clean_html(http_get(url, timeout=25))


def do_spy(url: str | None = None, save_snapshot_flag: bool = False,
           diff_slug: str | None = None, render_js: bool = False) -> dict:
    if diff_slug and not url:
        # mode banding: snapshot lama vs fetch ulang URL tersimpan
        con = sqlite3.connect(DB_PATH) if DB_PATH.exists() else None
        saved_url = None
        if con:
            try:
                row = con.execute("SELECT url FROM snapshots WHERE slug=?", (diff_slug,)).fetchone()
                saved_url = row[0] if row else None
            except Exception:
                pass
            con.close()
        old = load_snapshot(diff_slug)
        if old is None:
            return {"error": f"snapshot '{diff_slug}' tidak ditemukan di ./snapshots/."}
        if not saved_url:
            return {"error": f"URL asal snapshot '{diff_slug}' tak tercatat. Pakai: spy --url <url> --diff {diff_slug}"}
        new = scrape_url(saved_url, render_js)
        d = diff_texts(old, new)
        return {"mode": "diff", "slug": diff_slug, "url": saved_url, **d,
                "verdict": "YA" if d["changed"] else "TIDAK"}
    if not url:
        return {"error": "butuh --url atau --diff <slug>."}
    slug = slugify(urllib.parse.urlparse(url).netloc + urllib.parse.urlparse(url).path)
    new = scrape_url(url, render_js)
    out: dict = {"mode": "scrape", "slug": slug, "url": url, "chars": len(new),
                 "preview": new[:800], "sha": hashlib.sha256(new.encode()).hexdigest()[:16]}
    old = load_snapshot(slug)
    if save_snapshot_flag:
        out["saved_sha"] = save_snapshot(slug, url, new)
        out["saved_to"] = str(snapshot_path(slug))
    if old is not None and old != new:
        d = diff_texts(old, new)
        out.update({k: d[k] for k in ("changed", "ratio", "added", "removed", "highlights")})
        out["verdict"] = "YA" if d["changed"] else "TIDAK"
    elif old is not None:
        out.update({"changed": False, "ratio": 1.0, "added": 0, "removed": 0, "highlights": [], "verdict": "TIDAK"})
    return out


# ---------- Briefing ----------

def print_briefing(target: str, bluf: str, signals: list[str],
                   movements: list[str], actions: list[str], as_json: dict | None = None):
    today = date.today().isoformat()
    print("=" * 60)
    print(f"[ARGUS INTEL BRIEFING] - TANGGAL: {today}")
    print(f"TARGET: {target}")
    print("=" * 60)
    print("\n1. BOTTOM LINE UP FRONT (BLUF):")
    print(f"   - {bluf}")
    print("\n2. MARKET SIGNALS & TRENDS:")
    for s in signals or ["- (belum ada sinyal)"]:
        print(f"   - {s}")
    print("\n3. COMPETITOR MOVEMENTS (Jika ada):")
    for m in movements or ["- Tidak ada perubahan terdeteksi / tidak dimonitor."]:
        print(f"   - {m}")
    print("\n4. ACTIONABLE RECOMMENDATIONS:")
    for a in actions or ["- (tambahkan langkah taktis manual)"]:
        print(f"   - {a}")
    print("=" * 60)
    if as_json is not None:
        print("\n[JSON]")
        print(json.dumps(as_json, ensure_ascii=False, indent=2)[:6000])


def build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="research.py",
                                description="Argus single-file: trend + intel + spy -> Executive Briefing.")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("trend", help="Pilar 1: Google Trends + sentimen Reddit")
    t.add_argument("--query", required=True, help='cth: "SaaS HRIS Indonesia"')
    t.add_argument("--source", default="google,reddit")
    t.add_argument("--timeframe", default="7d", choices=["24h", "7d", "30d", "90d"])
    t.add_argument("--json", action="store_true")

    i = sub.add_parser("intel", help="Pilar 2: berita live + radar regulasi/pendanaan")
    i.add_argument("--industry", required=True, help='cth: "Fintech Lending"')
    i.add_argument("--topic", default="regulation,funding", help='cth: "regulation,funding,merger,lawsuit,policy"')
    i.add_argument("--max", type=int, default=10)
    i.add_argument("--json", action="store_true")

    s = sub.add_parser("spy", help="Pilar 3: scrape kompetitor + diff snapshot")
    s.add_argument("--url", default=None)
    s.add_argument("--save-snapshot", action="store_true")
    s.add_argument("--diff", default=None, help="slug snapshot, cth: competitor_com_pricing")
    s.add_argument("--render-js", action="store_true", help="paksa Playwright (default: urllib cepat)")
    s.add_argument("--json", action="store_true")

    b = sub.add_parser("brief", help="Gabungan 3 pilar -> 1 Executive Briefing")
    b.add_argument("--query", default="", help="kata kunci tren (Pilar 1)")
    b.add_argument("--industry", default="", help="industri intel (Pilar 2)")
    b.add_argument("--topic", default="regulation,funding")
    b.add_argument("--url", default="", help="URL kompetitor (Pilar 3)")
    b.add_argument("--json", action="store_true")
    return p


def main(argv=None):
    args = build_cli().parse_args(argv)
    db_init()

    if args.cmd == "trend":
        res = do_trend(args.query, args.source, args.timeframe)
        tr, se = res.get("trend", {}), res.get("sentiment", {})
        g = tr.get("growth_pct")
        sig = [f"Volume pencarian '{args.query}': "
               + (f"{'Naik' if g and g > 0 else 'Turun'} {g}% ({tr.get('source')})" if g is not None else "n/a (pytrends off)")]
        if tr.get("related"):
            sig.append("Related queries: " + ", ".join(tr["related"][:5]))
        if se:
            sig.append(f"Sentimen Reddit: {se.get('score')} (pos {se.get('pos')}/neu {se.get('neu')}/neg {se.get('neg')} dari {se.get('count')} post)")
            for pain in (se.get("top_pains") or [])[:3]:
                sig.append(f"Keluhan: {pain[:140]}")
        bluf = f"Tren '{args.query}' " + (f"{'naik' if g and g > 0 else 'turun'} {g}% " if g is not None else "belum terukur ") + \
               (f"dengan sentimen {se.get('score')}." if se else ".")
        print_briefing(args.query, bluf, sig, [], ["Validasi 3 keluhan teratas ke roadmap produk."], res if args.json else None)

    elif args.cmd == "intel":
        res = do_intel(args.industry, args.topic, args.max)
        sig = []
        for a in res["articles"][:3]:
            sig.append(f"{a['title'][:120]} — {a['url']} [hits: {','.join(a.get('topic_hits', [])) or '-'} | {a.get('threat_or_opportunity')}]")
        if not sig:
            sig = [f"Tidak ada artikel kuat untuk topik {args.topic} (raw: {res['count_raw']}). Longgarkan --topic."]
        bluf = f"Radar {args.industry}: {len(res['articles'])} sinyal relevan ({args.topic})."
        print_briefing(args.industry, bluf, sig, [], ["Petakan tiap sinyal ke ancaman/peluang kuartal ini."], res if args.json else None)

    elif args.cmd == "spy":
        res = do_spy(args.url, args.save_snapshot, args.diff, args.render_js)
        if "error" in res:
            print(f"ERROR: {res['error']}", file=sys.stderr)
            sys.exit(2)
        if res.get("mode") == "diff":
            print_briefing(res["url"], f"Perubahan terdeteksi: {res['verdict']} (similarity {res['ratio']}).",
                           [], (res.get("highlights") or ["Tidak ada highlight harga/fitur."]),
                           ["Review pricing page kompetitor bila verdict YA."], res if args.json else None)
        else:
            mv = []
            if "verdict" in res:
                mv.append(f"Status: {res['verdict']} (+{res.get('added',0)}/-{res.get('removed',0)} baris).")
                mv += (res.get("highlights") or [])[:5]
            else:
                mv.append(f"Snapshot awal disimpan ({res['chars']} chars). Bandingkan minggu depan via --diff {res['slug']}.")
            print_briefing(res["url"], f"Scrape {res['chars']} chars, sha {res['sha']}." +
                           (f" Snapshot: {res.get('saved_to')}" if res.get("saved_to") else ""),
                           [], mv, ["Jadwalkan spy mingguan untuk URL ini."], res if args.json else None)

    elif args.cmd == "brief":
        target = args.query or args.industry or args.url or "(tanpa target)"
        signals, movements = [], []
        raw: dict = {}
        if args.query:
            r1 = do_trend(args.query, "google,reddit", "7d")
            raw["trend"] = r1
            g = (r1.get("trend") or {}).get("growth_pct")
            se = (r1.get("sentiment") or {}).get("score")
            signals.append(f"Tren '{args.query}': growth {g}% | sentimen {se}")
            for pain in ((r1.get("sentiment") or {}).get("top_pains") or [])[:3]:
                signals.append(f"Keluhan: {pain[:140]}")
        if args.industry:
            r2 = do_intel(args.industry, args.topic)
            raw["intel"] = r2
            for a in r2["articles"][:3]:
                signals.append(f"{a['title'][:110]} — {a['url']}")
        if args.url:
            r3 = do_spy(args.url, True, None, False)
            raw["spy"] = {k: v for k, v in r3.items() if k != "preview"}
            movements.append(f"{args.url}: verdict {r3.get('verdict', 'baseline')} sha {r3.get('sha')}")
            movements += (r3.get("highlights") or [])[:5]
        bluf = " ".join(signals[:1]) or "Belum ada sinyal kuat — persempit query/industry/URL."
        print_briefing(target, bluf, signals, movements,
                       ["Diskusikan 1 ancaman + 1 peluang di meeting berikutnya."],
                       raw if args.json else None)


if __name__ == "__main__":
    main()
