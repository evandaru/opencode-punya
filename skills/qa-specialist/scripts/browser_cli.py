#!/usr/bin/env python3
"""browser_cli.py — Alternatif ringan lite.ego.app (tanpa browser bundelan).

Prioritas browser (tidak download baru kalau sudah ada):
  1. Brave sistem (/Applications/Brave Browser.app) via executable_path
  2. Chromium Playwright yang sudah terinstall (chromium-1200)
  3. agent-browser / chrome-devtools-mcp kalau user sudah pasang (didelegasikan)

Pakai profil persisten sendiri (login sekali, kepake terus) sbg pengganti
"Ego inherits Chrome data" tapi tanpa migrasi / tanpa ganggu tab utama.

Contoh:
  python3 browser_cli.py doctor
  python3 browser_cli.py snapshot https://example.com
  python3 browser_cli.py scrape https://example.com -o hasil.json
  python3 browser_cli.py run steps.json --profile-dir .browser-profile
  python3 browser_cli.py mcp        # cetak config MCP slim (hemat token)
  python3 browser_cli.py agent      # cara install agent-browser (Vercel)

Format steps.json (SATU sesi browser, hemat token mirip ego heredoc):
  [
    {"action": "goto", "url": "https://example.com/login"},
    {"action": "snapshot"},
    {"action": "fill", "ref": "@3", "text": "user@mail.com"},
    {"action": "click", "ref": "@5"},
    {"action": "wait", "text": "Dashboard", "timeout": 8000},
    {"action": "eval", "js": "document.title"},
    {"action": "shot", "path": "bukti.jpg"}
  ]
Aksi: goto, snapshot, click, fill, press, wait(text/time), eval, shot, url, text.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

BRAVE_MAC = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"

ENUM_JS = """
() => {
  const els = [...document.querySelectorAll(
    'a, button, input, select, textarea, [role="button"], [role="link"], [role="textbox"], [role="checkbox"]'
  )].filter(e => {
    const r = e.getBoundingClientRect();
    const st = getComputedStyle(e);
    return st.display !== 'none' && st.visibility !== 'hidden' && (r.width > 0 || r.height > 0 || e.tagName === 'INPUT');
  });
  return els.slice(0, 150).map((e, i) => {
    const ref = '@' + (i + 1);
    e.setAttribute('data-br-ref', ref);
    let name = (e.getAttribute('aria-label') || e.innerText || e.value || e.placeholder || e.name || e.tagName || '').trim().replace(/\\s+/g, ' ');
    let role = e.getAttribute('role') || e.tagName.toLowerCase();
    if (e.tagName === 'INPUT') role = 'input[' + (e.type || 'text') + ']';
    return {ref, role, name: name.slice(0, 80), tag: e.tagName};
  });
}
"""

FIND_JS = """(ref) => {
  const e = document.querySelector(`[data-br-ref="${ref}"]`);
  if (!e) return null;
  const r = e.getBoundingClientRect();
  return {tag: e.tagName, x: r.x, y: r.y};
}"""


def resolve_browser():
    """Return (kind, executable_path_or_None). Tidak download apa pun."""
    if Path(BRAVE_MAC).exists():
        return ("brave", BRAVE_MAC)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path
            if Path(path).exists():
                return ("playwright-chromium", None)
    except Exception:
        pass
    return ("none", None)


def launch_context(pw, args):
    """Persistent context ringan: headless default, profil sendiri."""
    profile = Path(args.profile_dir or "/tmp/browser-ringan-profile")
    profile.mkdir(parents=True, exist_ok=True)
    kind, exe = resolve_browser()
    kwargs = dict(
        user_data_dir=str(profile),
        headless=not args.headed,
        viewport={"width": 1280, "height": 800},
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    if args.browser == "brave" or (args.browser == "auto" and kind == "brave"):
        kwargs["executable_path"] = BRAVE_MAC
        kwargs["channel"] = None
    # else: default chromium Playwright (sudah ada, tanpa download)
    browser = pw.chromium.launch_persistent_context(**kwargs)
    page = browser.pages[0] if browser.pages else browser.new_page()
    page.set_default_timeout(15000)
    return browser, page, kind


def snapshot_text(page):
    # 1) coba accessibility tree bawaan (paling hemat token)
    try:
        tree = page.accessibility.snapshot()
        lines, counter = [], [0]

        def walk(node, depth=0):
            if not node or counter[0] >= 120:
                return
            role = node.get("role", "?")
            name = (node.get("name") or "").strip().replace("\n", " ")[:80]
            if role not in ("generic", "none") and (name or role in ("button", "link", "textbox", "heading")):
                counter[0] += 1
                lines.append(f"@{counter[0]} [{'heading' if role=='heading' else role}] \"{name}\"")
            for ch in node.get("children", []) or []:
                walk(ch, depth + 1)

        walk(tree)
        if len(lines) >= 3:
            return f"Page: {page.title()}\nURL: {page.url}\n" + "\n".join(lines)
    except Exception:
        pass
    # 2) fallback: enumerasi elemen interaktif via JS
    items = page.evaluate(ENUM_JS)
    lines = [f"{it['ref']} [{it['role']}] \"{it['name']}\"" for it in items]
    return f"Page: {page.title()}\nURL: {page.url}\n" + "\n".join(lines)


def ref_selector(page, ref):
    found = page.evaluate(FIND_JS, ref)
    if not found:
        page.evaluate(ENUM_JS)  # re-enumerate (DOM berubah)
        found = page.evaluate(FIND_JS, ref)
    if not found:
        raise RuntimeError(f"ref {ref} tidak ketemu — jalankan snapshot dulu")
    return f'[data-br-ref="{ref}"]'


def cmd_doctor(_args):
    kind, _ = resolve_browser()
    print(json.dumps({
        "browser": kind,
        "brave": Path(BRAVE_MAC).exists(),
        "agent-browser": shutil.which("agent-browser") is not None,
        "npx": shutil.which("npx") is not None,
        "playwright_py": _has("playwright"),
    }, indent=2))
    if kind == "none":
        print("Tidak ada browser. Pasang: playwright install chromium", file=sys.stderr)


def _has(mod):
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def cmd_snapshot(args):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser, page, kind = launch_context(pw, args)
        try:
            page.goto(args.url, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            print(f"# [{kind}] {page.url}")
            print(snapshot_text(page))
        finally:
            browser.close()


def cmd_scrape(args):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser, page, kind = launch_context(pw, args)
        try:
            page.goto(args.url, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            data = page.evaluate("""() => {
              const txt = document.body ? document.body.innerText : '';
              const clean = txt.replace(/\\n{3,}/g, '\\n\\n').trim();
              return {
                title: document.title,
                url: location.href,
                text: clean.slice(0, 8000),
                text_len: clean.length,
                headings: [...document.querySelectorAll('h1,h2,h3')].slice(0,20).map(e=>e.innerText.trim().slice(0,120)),
                links: [...document.querySelectorAll('a[href]')].slice(0,50).map(a=>({t:a.innerText.trim().slice(0,80), h:a.href}))
              };
            }""")
            out = json.dumps(data, ensure_ascii=False, indent=2)
            if args.output:
                Path(args.output).write_text(out, encoding="utf-8")
                print(f"OK -> {args.output} ({data['text_len']} chars)")
            else:
                print(out)
        finally:
            browser.close()


def cmd_run(args):
    from playwright.sync_api import sync_playwright
    steps = json.loads(Path(args.steps).read_text(encoding="utf-8"))
    log = []
    with sync_playwright() as pw:
        browser, page, _ = launch_context(pw, args)
        try:
            for i, s in enumerate(steps):
                a = s.get("action")
                try:
                    if a == "goto":
                        page.goto(s["url"], wait_until="domcontentloaded")
                        page.wait_for_timeout(1000)
                        log.append({"step": i, "action": "goto", "url": page.url})
                    elif a == "snapshot":
                        log.append({"step": i, "action": "snapshot", "text": snapshot_text(page)})
                    elif a == "click":
                        page.click(ref_selector(page, s["ref"]))
                        page.wait_for_timeout(1000)
                        log.append({"step": i, "action": "click", "ref": s["ref"], "url": page.url})
                    elif a == "fill":
                        page.fill(ref_selector(page, s["ref"]), s.get("text", ""))
                        log.append({"step": i, "action": "fill", "ref": s["ref"]})
                    elif a == "press":
                        page.keyboard.press(s.get("key", "Enter"))
                        page.wait_for_timeout(800)
                        log.append({"step": i, "action": "press", "key": s.get("key")})
                    elif a == "wait":
                        if "text" in s:
                            page.get_by_text(s["text"]).first.wait_for(timeout=s.get("timeout", 8000))
                        else:
                            page.wait_for_timeout(s.get("ms", 1500))
                        log.append({"step": i, "action": "wait", "ok": True})
                    elif a == "eval":
                        log.append({"step": i, "action": "eval", "result": str(page.evaluate(s["js"]))[:2000]})
                    elif a == "shot":
                        # jpeg default: 3-5x lebih kecil dari png (hemat token/hemat disk)
                        page.screenshot(path=s.get("path", "shot.jpg"), type="jpeg", quality=60)
                        log.append({"step": i, "action": "shot", "path": s.get("path", "shot.jpg")})
                    elif a in ("url", "text"):
                        log.append({"step": i, "action": a,
                                    "result": page.url if a == "url" else page.inner_text("body")[:4000]})
                    else:
                        log.append({"step": i, "error": f"aksi tak dikenal: {a}"})
                except Exception as e:
                    # Jangan hanguskan log: catat gagalnya, hentikan run, tetap simpan parsial
                    log.append({"step": i, "action": a, "FAILED": f"{type(e).__name__}: {str(e)[:300]}", "url": page.url})
                    break
        finally:
            browser.close()
    out = json.dumps(log, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"OK {len(log)} langkah -> {args.output}")
    else:
        print(out)


def cmd_mcp(_args):
    print(json.dumps({"mcpServers": {
        "playwright-slim": {"command": "npx", "args": ["@playwright/mcp@latest", "--headless", "--browser=chromium"]},
        "chromedev-slim": {"command": "npx", "args": ["chrome-devtools-mcp@latest", "--slim", "--screenshot-format=jpeg"]}
    }}, indent=2))
    print("\n# Tempel ke MCP client (opencode/VSCode/Cursor). chromedev-slim paling ringan (3 tools).")


def cmd_agent(_args):
    print("""# agent-browser (Vercel) — CLI Rust, pakai Chrome yang sudah ada, tanpa browser bundelan
npm install -g agent-browser
agent-browser --profile Default open https://gmail.com   # pinjam login (read-only copy)
npx skills add vercel-labs/agent-browser   # pasang skill resminya
# Ringan serverless: AGENT_BROWSER_EXECUTABLE_PATH pakai @sparticuz/chromium (~50MB vs ~684MB)""")


def add_common(p):
    p.add_argument("--browser", default="auto", choices=["auto", "brave", "chromium"])
    p.add_argument("--headed", action="store_true", help="tampilkan jendela (default headless)")
    p.add_argument("--profile-dir", default="", help="folder profil persisten (default /tmp/browser-ringan-profile)")
    return p


def main():
    ap = argparse.ArgumentParser(description="browser_cli: E2E ringan qa-specialist tanpa browser bundelan")
    add_common(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("doctor", help="cek browser & tools yang tersedia")
    p.set_defaults(fn=cmd_doctor)
    p = add_common(sub.add_parser("snapshot", help="buka URL, cetak snapshot @refs hemat token"))
    p.add_argument("url"); p.set_defaults(fn=cmd_snapshot)
    p = add_common(sub.add_parser("scrape", help="ambil teks bersih + links sbg JSON"))
    p.add_argument("url"); p.add_argument("-o", "--output", default="")
    p.set_defaults(fn=cmd_scrape)
    p = add_common(sub.add_parser("run", help="jalankan steps.json dalam SATU sesi browser"))
    p.add_argument("steps"); p.add_argument("-o", "--output", default="")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("mcp", help="cetak config MCP slim")
    p.set_defaults(fn=cmd_mcp)
    p = sub.add_parser("agent", help="cara install agent-browser Vercel")
    p.set_defaults(fn=cmd_agent)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
