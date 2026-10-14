---
name: argus-research
description: |
  Market intel and competitor research CLI (Project Argus, research.py). Use when the user says riset pasar, trend hunter, intel kompetitor, spy pricing, cek perubahan website kompetitor, radar regulasi, pendanaan startup, sentimen reddit, executive briefing, BLUF, argus, or research brief.
---

# Argus Research (Market Intel & Trend Hunter)

Single-file CLI skill: tren + sentimen sosial, live intel regulasi/pendanaan,
dan spy kompetitor (scrape + diff snapshot). Output selalu
**Executive Briefing (BLUF)** siap tempel ke meeting note.

Script: `research.py` (folder skill ini). Python 3.10+, tanpa API key tetap jalan.

## Cara pakai (agen)

Jalankan dari project user, arahkan data snapshot ke project (bukan ke folder skill):

```bash
export ARGUS_DATA_DIR="$PWD/.argus"
python3 ~/.config/opencode/skills/argus-research/research.py --help
```

Tanpa `ARGUS_DATA_DIR`, `snapshots/` + `argus.db` tersimpan di sebelah script
(folder skill global) — hindari itu untuk data milik project.

## 1. Pilar 1 — Tren & sentimen

```bash
python3 ~/.config/opencode/skills/argus-research/research.py trend \
  --query "SaaS HRIS Indonesia" --source google,reddit --timeframe 7d
```

- `--timeframe`: `24h | 7d | 30d | 90d`. Offline-aman: `--source google` saja.
- Hasil: growth % pencarian, skor sentimen -1.0..+1.0, top 3 keluhan user.

## 2. Pilar 2 — Live intel & regulatory radar

```bash
python3 ~/.config/opencode/skills/argus-research/research.py intel \
  --industry "Fintech Lending" --topic "regulation,funding"
```

- `--topic`: subset dari `regulation,funding,merger,lawsuit,policy`.
- Hasil: maks 3 sinyal kredibel + link + label ancaman/peluang. Tanpa hasil =
  longgarkan `--topic`.

## 3. Pilar 3 — Spy kompetitor & change tracker

```bash
python3 ~/.config/opencode/skills/argus-research/research.py spy \
  --url "https://competitor.com/pricing" --save-snapshot
python3 ~/.config/opencode/skills/argus-research/research.py spy \
  --diff competitor_com_pricing
```

- Default scrape cepat (urllib + rotasi User-Agent). JS berat: tambah `--render-js`
  (butuh `playwright install chromium`).
- Slug = nama domain+path yang di-slugify, terlihat di output scrape pertama.
- Verdict `YA/TIDAK` + highlight harga/fitur (`Rp`, `%`, `gratis`, `pro/basic`,
  `unlimited`, `AI/chatbot`).

## 4. Gabungan — satu briefing

```bash
python3 ~/.config/opencode/skills/argus-research/research.py brief \
  --query "SaaS HRIS Indonesia" --industry "SaaS HRIS" \
  --url "https://competitor.com/pricing"
```

Tambah `--json` di perintah apa pun untuk data mentah (maks ~6000 char).

## Format output (jangan diubah)

Pertahankan struktur ini persis untuk CEO:

```
[ARGUS INTEL BRIEFING] - TANGGAL: YYYY-MM-DD
TARGET: ...
1. BOTTOM LINE UP FRONT (BLUF): satu kalimat paling krusial
2. MARKET SIGNALS & TRENDS
3. COMPETITOR MOVEMENTS
4. ACTIONABLE RECOMMENDATIONS
```

## Batasan yang perlu disampaikan ke user

- Tanpa `pytrends`, growth % jadi `n/a` — pakai sinyal Reddit/news sebagai proksi.
  Pasang via `pip install pytrends duckduckgo-search trafilatura beautifulsoup4`.
- Reddit/berita butuh network; di macOS Python yang belum install cert akan muncul
  `SSL CERTIFICATE_VERIFY_FAILED` — fix: jalankan `Install Certificates.command`
  bawaan Python atau `pip install -U certifi`.
- Target NFR PRD: live search <8 dtk, scrape <30 dtk; teks dibersihkan agar hemat
  token (~70%).
- Jadwalkan `spy --save-snapshot` mingguan per URL agar `--diff` bermakna.
