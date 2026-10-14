import argparse
import json
import re
import time
from collections import deque
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Ekstensi file statis yang di-skip saat crawl (bukan halaman HTML)
SKIP_EXTENSIONS = (
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp4", ".mp3",
    ".pdf", ".zip", ".xml", ".txt",
)

SCROLL_JS = """
async () => {
    await new Promise((resolve) => {
        let totalHeight = 0;
        let distance = 300;
        let timer = setInterval(() => {
            let scrollHeight = document.body.scrollHeight;
            window.scrollBy(0, distance);
            totalHeight += distance;
            if(totalHeight >= scrollHeight){
                clearInterval(timer);
                resolve();
            }
        }, 100);
    });
}
"""


def normalize_url(url: str) -> str:
    """Hilangkan fragment (#...) dan rapikan URL untuk dedup visited."""
    url, _ = urldefrag(url.strip())
    return url


def norm_netloc(netloc: str) -> str:
    """fauzanhasyim.my.id dan www.fauzanhasyim.my.id dianggap domain yang sama."""
    return netloc.lower().removeprefix("www.")


def is_internal(url: str, base_netloc: str) -> bool:
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    # Terima www vs non-www sebagai internal
    if norm_netloc(p.netloc) != norm_netloc(base_netloc):
        return False
    path_low = p.path.lower()
    if path_low.endswith(SKIP_EXTENSIONS):
        return False
    return True


def url_to_filepath(output_dir: Path, url: str, ext: str = ".html") -> Path:
    """Mapping URL -> file dengan struktur folder dipertahankan.

    / -> index.html / index.json
    /id/about -> id/about.html / id/about.json
    /id/about/ -> id/about.html
    """
    p = urlparse(url)
    path = p.path.strip("/")
    if not path:
        rel = Path(f"index{ext}")
    else:
        rel = Path(path)
        # /foo -> foo.html, /foo/bar -> foo/bar.html
        if not rel.suffix:
            rel = rel.with_suffix(ext)
        elif rel.suffix.lower() != ext:
            rel = rel.with_suffix(rel.suffix + ext)
    # Amankan query string jadi suffix file: /blog?page=2 -> blog__page-2.html
    if p.query:
        safe_q = re.sub(r"[^a-zA-Z0-9_-]+", "-", p.query).strip("-")[:60]
        rel = rel.with_name(f"{rel.stem}__{safe_q}{rel.suffix}")
    return output_dir / rel


def clean_text(text: str) -> str:
    """Rapikan innerText: collapse whitespace, buang baris kosong berlebihan."""
    text = re.sub(r"\r", "\n", text or "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


PAGE_EXTRACT_JS = """() => {
  const text_of = (el) => (el ? (el.innerText || el.textContent || '') : '');
  const abs = (u) => { try { return new URL(u, document.baseURI).href; } catch { return u || ''; } };
  const meta_content = (sel) => document.querySelector(sel)?.content || '';
  const all_meta = {};
  document.querySelectorAll('meta').forEach(m => {
    const k = m.getAttribute('name') || m.getAttribute('property') || m.getAttribute('itemprop') || m.getAttribute('charset') ? (m.getAttribute('name') || m.getAttribute('property') || m.getAttribute('itemprop') || 'charset') : '';
    if (k) all_meta[k] = m.content || m.getAttribute('content') || '';
  });
  const og = {}; const tw = {};
  document.querySelectorAll('meta[property^="og:"]').forEach(m => { og[m.getAttribute('property')] = m.content || ''; });
  document.querySelectorAll('meta[name^="twitter:"]').forEach(m => { tw[m.getAttribute('name')] = m.content || ''; });
  const json_ld = [];
  document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
    try { json_ld.push(JSON.parse(s.textContent)); } catch { json_ld.push(s.textContent.slice(0, 2000)); }
  });
  const headings = { h1: [], h2: [], h3: [] };
  ['h1','h2','h3'].forEach(tag => {
    document.querySelectorAll(tag).forEach(el => {
      const t = text_of(el).replace(/\\s+/g, ' ').trim();
      if (t) headings[tag].push(t);
    });
  });
  const paragraphs = [];
  document.querySelectorAll('p').forEach(el => {
    const t = text_of(el).replace(/\\s+/g, ' ').trim();
    if (t) paragraphs.push(t);
  });
  const list_items = [];
  document.querySelectorAll('ul li, ol li').forEach(el => {
    const t = text_of(el).replace(/\\s+/g, ' ').trim();
    if (t) list_items.push(t);
  });
  const tables = [];
  document.querySelectorAll('table').forEach(tbl => {
    const rows = [];
    tbl.querySelectorAll('tr').forEach(tr => {
      const cells = Array.from(tr.querySelectorAll('th, td')).map(c => text_of(c).replace(/\\s+/g, ' ').trim());
      if (cells.some(c => c)) rows.push(cells);
    });
    if (rows.length) tables.push({ rows: rows.slice(0, 50) });
    if (tables.length >= 10) return;
  });
  const images = [];
  document.querySelectorAll('img').forEach(img => {
    const src = abs(img.getAttribute('src') || img.currentSrc || '');
    if (!src) return;
    images.push({ src, alt: img.alt || '' });
    if (images.length >= 100) return;
  });
  const videos = [];
  document.querySelectorAll('video source[src], video[src]').forEach(v => {
    const src = abs(v.getAttribute('src') || '');
    if (src) videos.push(src);
  });
  const audios = [];
  document.querySelectorAll('audio source[src], audio[src]').forEach(a => {
    const src = abs(a.getAttribute('src') || '');
    if (src) audios.push(src);
  });
  const raw_links = Array.from(document.querySelectorAll('a[href]'), a => a.href);
  const buttons = [];
  document.querySelectorAll('button').forEach(b => {
    const t = text_of(b).replace(/\\s+/g, ' ').trim();
    if (t) buttons.push(t);
  });
  return {
    title: document.title || '',
    lang: document.documentElement.lang || '',
    description: meta_content('meta[name="description"]'),
    keywords: meta_content('meta[name="keywords"]'),
    author: meta_content('meta[name="author"]'),
    robots: meta_content('meta[name="robots"]'),
    canonical: abs(document.querySelector('link[rel="canonical"]')?.href || ''),
    meta: all_meta,
    open_graph: og,
    twitter: tw,
    json_ld: json_ld.slice(0, 5),
    headings,
    paragraphs: paragraphs.slice(0, 200),
    list_items: list_items.slice(0, 200),
    tables: tables.slice(0, 10),
    images: images.slice(0, 100),
    videos: videos.slice(0, 20),
    audios: audios.slice(0, 20),
    raw_links,
    buttons: buttons.slice(0, 50),
    text: document.body ? (document.body.innerText || '') : '',
  };
}"""


def extract_page_data(page, url: str, depth: int = 0) -> dict:
    """Ambil data selengkap mungkin siap-JSON dari halaman yang sudah ter-render.

    Skema:
    {url, final_url, canonical, lang, title, description, keywords, author,
     robots, meta, open_graph, twitter, json_ld, headings, paragraphs,
     list_items, tables, images, videos, audios, buttons,
     text, text_len, word_count, reading_time_min,
     links, links_internal, links_external, emails,
     depth, crawled_at}
    """
    from datetime import datetime, timezone

    try:
        raw = page.evaluate(PAGE_EXTRACT_JS)
    except Exception:
        raw = {}
    try:
        final_url = page.url
    except Exception:
        final_url = url

    base_netloc = urlparse(url).netloc
    text = clean_text(raw.get("text", ""))

    # Normalisasi + dedup links, pisahkan internal/eksternal/email
    seen: set[str] = set()
    links: list[str] = []
    internal: list[str] = []
    external: list[str] = []
    emails: list[str] = []
    for h in raw.get("raw_links", []) or []:
        if not h:
            continue
        if h.startswith("mailto:"):
            em = h.removeprefix("mailto:").split("?")[0].strip()
            if em and em not in emails:
                emails.append(em)
            continue
        if h.startswith(("javascript:", "tel:", "data:")):
            continue
        h = normalize_url(h)
        if not h or h in seen:
            continue
        seen.add(h)
        links.append(h)
        try:
            kind = "internal" if norm_netloc(urlparse(h).netloc) == norm_netloc(base_netloc) else "external"
        except Exception:
            kind = "external"
        (internal if kind == "internal" else external).append(h)
    links.sort()
    internal.sort()
    external.sort()
    emails.sort()

    words = len(text.split()) if text else 0
    return {
        "url": url,
        "final_url": normalize_url(final_url or url),
        "canonical": normalize_url(raw.get("canonical", "") or ""),
        "lang": raw.get("lang", "") or "",
        "title": raw.get("title", "") or "",
        "description": raw.get("description", "") or "",
        "keywords": raw.get("keywords", "") or "",
        "author": raw.get("author", "") or "",
        "robots": raw.get("robots", "") or "",
        "meta": raw.get("meta", {}) or {},
        "open_graph": raw.get("open_graph", {}) or {},
        "twitter": raw.get("twitter", {}) or {},
        "json_ld": raw.get("json_ld", []) or [],
        "headings": raw.get("headings", {"h1": [], "h2": [], "h3": []}),
        "paragraphs": raw.get("paragraphs", []) or [],
        "list_items": raw.get("list_items", []) or [],
        "tables": raw.get("tables", []) or [],
        "images": raw.get("images", []) or [],
        "videos": raw.get("videos", []) or [],
        "audios": raw.get("audios", []) or [],
        "buttons": raw.get("buttons", []) or [],
        "text": text,
        "text_len": len(text),
        "word_count": words,
        "reading_time_min": round(words / 200, 2) if words else 0,
        "links": links,
        "links_internal": internal,
        "links_external": external,
        "emails": emails,
        "depth": depth,
        "crawled_at": datetime.now(timezone.utc).isoformat(),
    }


def default_single_filename(url: str) -> str:
    """output-[nama-web].json, mis. https://fauzanhasyim.my.id -> output-fauzanhasyim.my.id.json"""
    netloc = urlparse(url).netloc.lower().removeprefix("www.")
    safe = re.sub(r"[^a-zA-Z0-9.-]+", "-", netloc).strip("-") or "site"
    return f"output-{safe}.json"


def auto_scroll(page, quiet: bool = False):
    if not quiet:
        print("[*] Melakukan auto-scroll untuk memuat konten dinamis...")
    page.evaluate(SCROLL_JS)
    time.sleep(2)


def scrape_website(url: str, output_path: str, output_format: str = "html"):
    print(f"[*] Membuka browser untuk: {url} [format={output_format}]")

    with sync_playwright() as p:
        # Menjalankan browser chromium tanpa tampilan (headless)
        browser = p.chromium.launch(headless=True)
        # Atur ukuran layar agar web merender versi desktop (bukan mobile)
        context = browser.new_context(
            user_agent=DESKTOP_UA,
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        try:
            # networkidle = menunggu sampai tidak ada lagi fetch/request data background (JS selesai bekerja)
            page.goto(url, wait_until="networkidle", timeout=60000)

            # Trik: Scroll ke paling bawah untuk memicu 'lazy loading' (gambar/teks yg baru muncul pas discroll)
            auto_scroll(page)

            out = Path(output_path)
            # Tentukan file target per format (dukung -o tanpa ekstensi)
            if output_format in ("html", "both"):
                html_path = out if out.suffix == ".html" else out.with_suffix(".html") if output_format == "html" else out
                # kalau -o adalah folder saat both, simpan index.html di dalamnya
                if output_format == "both" and out.suffix == "":
                    html_path = out / "index.html"
                    html_path.parent.mkdir(parents=True, exist_ok=True)
                rendered_html = page.content()
                html_path.parent.mkdir(parents=True, exist_ok=True)
                html_path.write_text(rendered_html, encoding="utf-8")
                print(f"[✓] HTML disimpan di: {html_path}")

            if output_format in ("json", "both"):
                data = extract_page_data(page, url, depth=0)
                if output_format == "json":
                    json_path = out if out.suffix == ".json" else out.with_suffix(".json") if out.suffix else Path(str(out) + ".json")
                    # -o dataset.html + --format json -> dataset.json
                    if json_path.suffix == ".html":
                        json_path = json_path.with_suffix(".json")
                else:
                    base = out if out.suffix == "" else out.parent
                    json_path = base / "index.json" if out.suffix == "" else out.with_suffix(".json") if out.suffix == ".html" else Path(str(out) + ".json")
                json_path.parent.mkdir(parents=True, exist_ok=True)
                json_path.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                print(f"[✓] JSON disimpan di: {json_path} ({len(data['text'])} char teks)")

        except PlaywrightTimeoutError:
            print(
                "[!] Error: Halaman terlalu lambat dimuat (Timeout). Mencoba mengambil konten seadanya..."
            )
            try:
                if output_format in ("html", "both"):
                    Path(output_path).write_text(page.content(), encoding="utf-8")
                if output_format in ("json", "both"):
                    data = extract_page_data(page, url, depth=0)
                    Path(str(Path(output_path).with_suffix(".json"))).write_text(
                        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
            except Exception as e:
                print(f"[X] Gagal simpan darurat: {e}")
        except Exception as e:
            print(f"[X] Gagal mengambil data: {e}")
        finally:
            browser.close()


def crawl_website(
    start_url: str,
    output_dir: str,
    max_pages: int = 20,
    max_depth: int = 2,
    delay: float = 1.0,
    list_only: bool = False,
    output_format: str = "html",
):
    """Crawl BFS semua halaman internal mulai dari start_url.

    - Hanya domain yang sama (same-domain, www dianggap sama)
    - Skip file statis (css/js/img/font/video)
    - format html: tiap halaman *.html + urls.txt + sitemap.json (output = folder)
    - format json + output folder: tiap halaman *.json + dataset.json + urls.txt + sitemap.json
    - format json + output file .json: SATU FILE aja output-[nama-web].json berisi list semua halaman
    - format both: keduanya (output = folder)
    """
    start_url = normalize_url(start_url)
    base_netloc = urlparse(start_url).netloc
    single_file = output_format == "json" and str(output_dir).endswith(".json")
    out = Path(output_dir)
    if single_file:
        out.parent.mkdir(parents=True, exist_ok=True)
    elif not list_only:
        out.mkdir(parents=True, exist_ok=True)

    print(f"[*] Mulai crawl dari: {start_url}")
    print(f"[*] Domain: {base_netloc} | max_pages={max_pages} max_depth={max_depth} format={output_format}" + (" [single-file]" if single_file else ""))

    visited: set[str] = set()
    results: list[dict] = []
    pages_data: list[dict] = []
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=DESKTOP_UA,
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        try:
            while queue and len(visited) < max_pages:
                url, depth = queue.popleft()
                url = normalize_url(url)
                if url in visited:
                    continue
                if depth > max_depth:
                    continue
                visited.add(url)

                print(f"[*] [{len(visited)}/{max_pages}] (depth {depth}) {url}")
                try:
                    page.goto(url, wait_until="networkidle", timeout=60000)
                    auto_scroll(page, quiet=True)
                    data = extract_page_data(page, url, depth=depth)
                    title = data["title"]

                    rel_file = ""
                    if not list_only:
                        if single_file:
                            # Mode satu file: kumpulkan aja, tulis sekali di akhir
                            pages_data.append(data)
                        else:
                            if output_format in ("html", "both"):
                                fp = url_to_filepath(out, url, ext=".html")
                                fp.parent.mkdir(parents=True, exist_ok=True)
                                fp.write_text(page.content(), encoding="utf-8")
                                rel_file = str(fp.relative_to(out))
                            if output_format in ("json", "both"):
                                fp_json = url_to_filepath(out, url, ext=".json")
                                fp_json.parent.mkdir(parents=True, exist_ok=True)
                                fp_json.write_text(
                                    json.dumps(data, indent=2, ensure_ascii=False),
                                    encoding="utf-8",
                                )
                                if output_format == "json":
                                    rel_file = str(fp_json.relative_to(out))
                                pages_data.append(data)

                    entry = {"url": url, "depth": depth, "title": title, "file": rel_file}
                    # Untuk index sitemap, kalau json-only simpan panjang teks biar ringkas
                    if output_format == "json" and not list_only:
                        entry["text_len"] = len(data["text"])
                    results.append(entry)

                    for h in data["links"]:
                        if h in visited:
                            continue
                        if not is_internal(h, base_netloc):
                            continue
                        # Jangan antre duplikat
                        if any(h == q[0] for q in queue):
                            continue
                        queue.append((h, depth + 1))

                    if delay > 0:
                        time.sleep(delay)

                except PlaywrightTimeoutError:
                    print(f"[!] Timeout di {url}, skip...")
                    results.append(
                        {"url": url, "depth": depth, "title": "", "file": "", "error": "timeout"}
                    )
                except Exception as e:
                    print(f"[X] Gagal {url}: {e}")
                    results.append(
                        {"url": url, "depth": depth, "title": "", "file": "", "error": str(e)}
                    )
        finally:
            browser.close()

    # Simpan indeks hasil
    print(f"\n[✓] Crawl selesai. Total unik dikunjungi: {len(visited)}")
    for r in results:
        print(f" - {r['url']}  |  {r.get('title','')[:60]}  ->  {r.get('file','')}")

    if not list_only:
        if single_file:
            out.write_text(
                json.dumps(pages_data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"[✓] SATU FILE: {out} ({len(pages_data)} halaman)")
            return pages_data
        (out / "urls.txt").write_text(
            "\n".join(r["url"] for r in results) + "\n", encoding="utf-8"
        )
        (out / "sitemap.json").write_text(
            json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if output_format in ("json", "both") and pages_data:
            (out / "dataset.json").write_text(
                json.dumps(pages_data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"[✓] dataset.json: {len(pages_data)} halaman, hemat token siap training")
        print(f"[✓] Disimpan di folder: {out}/ (urls.txt + sitemap.json" + (" + dataset.json + *.json" if output_format in ("json", "both") else " + *.html") + ")")
    return results


def main():
    parser = argparse.ArgumentParser(
        description="CLI Advanced Web Dumper untuk Bahan Training AI"
    )
    parser.add_argument(
        "-u", "--url", required=True, help="URL website yang ingin di-scrape"
    )
    parser.add_argument(
        "-o",
        "--output",
        default="",
        help="Single-page: file output. Crawl html/both: folder. Crawl json: folder ATAU satu file .json (mis. output-web.json). Default otomatis.",
    )
    parser.add_argument(
        "--crawl",
        action="store_true",
        help="Aktifkan mode crawl: ikuti semua link internal (mis. /about, /blog, ...)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=20,
        help="Maksimal halaman saat crawl (default: 20)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=2,
        help="Maksimal kedalaman link dari URL awal (default: 2, 0=hanya homepage)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Jeda detik antar halaman saat crawl (default: 1.0)",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Hanya list URL internal tanpa download HTML (dipakai bareng --crawl)",
    )
    parser.add_argument(
        "--format",
        choices=["html", "json", "both"],
        default="html",
        help="Format output: html (mentah), json (bersih siap training AI), both (keduanya). Default: html",
    )

    args = parser.parse_args()

    # Pastikan url ada http/https
    url = args.url
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    if args.crawl:
        output = args.output or ""
        if not output:
            # Default otomatis: json -> satu file output-[nama-web].json, sisanya folder
            if args.format == "json" and not args.list_only:
                output = default_single_filename(url)
            else:
                output = "site_dump"
        crawl_website(
            start_url=url,
            output_dir=output,
            max_pages=args.max_pages,
            max_depth=args.max_depth,
            delay=args.delay,
            list_only=args.list_only,
            output_format=args.format,
        )
    else:
        out = args.output or ("dataset.json" if args.format == "json" else "dataset.html")
        scrape_website(url, out, output_format=args.format)


if __name__ == "__main__":
    main()
