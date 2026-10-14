---
name: curl-advanced
description: |
  Headless-browser web scraper (Playwright + Chromium) that renders JavaScript and dumps pages to HTML or clean JSON for AI training data. Use when the user says scrape website, dump web, ambil isi web, crawling halaman, daftar halaman internal, sitemap link, output json satu file, scraper_cli.py, curl advanced, render JS, lazy loading, or bahan training AI dari website.
---

# curl-advanced (Web Dumper)

Scrape website yang dirender JavaScript (React/Next.js/Vue) memakai
headless Chromium. Hasilnya HTML mentah atau JSON bersih siap training AI.
Script: `scripts/scraper_cli.py` (dibundel di skill ini).

## Prasyarat (sekali saja)

```bash
pip install playwright
python scripts/scraper_cli.py --help   # dari folder skill, atau:
playwright install chromium
```

> Kalau `playwright` belum ada browsernya (`chromium-1200` belum terdownload),
> jalankan `playwright install chromium` (~250MB).

## 1. Single page — ambil 1 URL

```bash
# HTML mentah (sudah ter-render JS + auto-scroll lazy loading)
python scripts/scraper_cli.py -u https://example.com -o hasil.html

# JSON bersih (teks tanpa tag, hemat token)
python scripts/scraper_cli.py -u https://example.com -o hasil.json --format json

# Keduanya
python scripts/scraper_cli.py -u https://example.com -o hasil --format both
```

## 2. Crawl — daftar + unduh semua halaman internal (/about, /blog, ...)

```bash
# Hanya list URL internal (tanpa download)
python scripts/scraper_cli.py -u https://example.com --crawl --list-only

# SATU FILE JSON: output-[nama-web].json (default, disarankan untuk training AI)
python scripts/scraper_cli.py -u https://example.com --crawl --format json

# Nama file sendiri
python scripts/scraper_cli.py -u https://example.com --crawl --format json -o output-websaya.json

# Mode folder lama (per-halaman + dataset.json + sitemap.json + urls.txt)
python scripts/scraper_cli.py -u https://example.com --crawl --format json -o site_json
python scripts/scraper_cli.py -u https://example.com --crawl -o site_dump   # HTML per halaman
```

Aturan `-o` saat crawl: diakhiri `.json` = single file, tanpa ekstensi = folder.
Tanpa `-o` + `--format json` = otomatis `output-[domain].json`.

## Opsi penting

| Flag | Default | Arti |
|---|---|---|
| `-u/--url` | wajib | URL awal (boleh tanpa `https://`) |
| `-o/--output` | otomatis | File (single) atau folder (multi) |
| `--crawl` | off | Ikuti link internal (BFS, same-domain, `www` dianggap sama) |
| `--max-pages` | 20 | Batas halaman saat crawl |
| `--max-depth` | 2 | Kedalaman link dari URL awal (`0` = homepage saja) |
| `--delay` | 1.0 | Jeda detik antar halaman |
| `--list-only` | off | List URL saja (pakai bareng `--crawl`) |
| `--format` | `html` | `html` mentah \| `json` bersih \| `both` |

## Skema JSON per halaman (31 keys)

Identitas: `url`, `final_url` (setelah redirect), `canonical`, `lang`,
`depth`, `crawled_at`. SEO: `title`, `description`, `keywords`, `author`,
`robots`, `meta`, `open_graph`, `twitter`, `json_ld`. Konten: `text`,
`text_len`, `word_count`, `reading_time_min`, `headings {h1,h2,h3}`,
`paragraphs[]`, `list_items[]`, `tables[]`, `buttons[]`. Media:
`images[] {src,alt}`, `videos[]`, `audios[]`. Link terpisah:
`links`, `links_internal`, `links_external`, `emails[]`.

## Batasan yang perlu disampaikan ke user

- Hanya domain yang sama yang di-crawl; link eksternal dicatat tapi tidak diikuti.
- File statis (css/js/img/font/video/pdf) di-skip saat crawl.
- `networkidle` 60 detik: situs sangat lambat disimpan seadanya (fallback).
- Selalu verifikasi: cek jumlah halaman + `word_count` sebelum diserahkan.
