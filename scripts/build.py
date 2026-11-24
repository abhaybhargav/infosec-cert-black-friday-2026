#!/usr/bin/env python3
"""Build README.md + data exports from data/deals.yml.

Part of infosec-cert-black-friday-2026 by v3nomtech (https://github.com/v3nomtech).

Usage:
    python scripts/build.py            # regenerate README.md, data/deals.json, data/deals.csv
    python scripts/build.py --check    # validate only; exit 1 on errors (used in CI for PRs)
    python scripts/build.py --today 2026-11-28   # pretend it's another day (for testing)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "deals.yml"
README = ROOT / "README.md"
START, END = "<!-- DEALS:START -->", "<!-- DEALS:END -->"

CATEGORIES = {
    "certification": "🎓 Certifications & Exam Vouchers",
    "training": "📚 Training & Courses",
    "labs": "🧪 Labs & Platforms",
}
REQUIRED = ("vendor", "category", "offer", "discount", "url", "source", "last_checked")
AFFILIATE_HINTS = ("ref=", "aff=", "affiliate", "utm_", "via=", "fpr=", "tag=")


# ───────────────────────────── helpers ─────────────────────────────
def as_date(value) -> dt.date | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def status(deal: dict, today: dt.date) -> tuple[str, str]:
    """Return (sort_key, label)."""
    if deal.get("expected"):
        return "4", "👀 Not announced"
    starts, ends = as_date(deal.get("starts")), as_date(deal.get("ends"))
    if ends and ends < today:
        return "5", "❌ Expired"
    if starts and starts > today:
        return "2", f"⏳ Starts {starts:%b %d}"
    if ends and (ends - today).days <= 2:
        return "0", "🔥 Ending soon"
    return "1", "✅ Live"


def fmt_date(value) -> str:
    d = as_date(value)
    return f"{d:%b %d, %Y}" if d else "—"


def cell(text) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


# ───────────────────────────── validation ─────────────────────────────
def validate(deals: list[dict]) -> list[str]:
    errors, seen = [], set()
    for i, d in enumerate(deals):
        tag = f"deal #{i + 1} ({d.get('vendor', '?')})"
        for key in REQUIRED:
            if not d.get(key):
                errors.append(f"{tag}: missing required field '{key}'")
        if d.get("category") not in CATEGORIES:
            errors.append(f"{tag}: category must be one of {list(CATEGORIES)}")
        if d.get("source") not in ("vendor", "third-party"):
            errors.append(f"{tag}: source must be 'vendor' or 'third-party'")
        for key in ("starts", "ends", "last_checked"):
            try:
                as_date(d.get(key))
            except ValueError:
                errors.append(f"{tag}: '{key}' must be YYYY-MM-DD")
        try:
            s, e = as_date(d.get("starts")), as_date(d.get("ends"))
            if s and e and e < s:
                errors.append(f"{tag}: ends before it starts")
        except ValueError:
            pass
        url = str(d.get("url", ""))
        if urlparse(url).scheme != "https":
            errors.append(f"{tag}: url must be https")
        if any(h in url.lower() for h in AFFILIATE_HINTS):
            errors.append(f"{tag}: url looks like an affiliate/tracking link — use the plain vendor URL")
        if "featured" in d and not isinstance(d["featured"], bool):
            errors.append(f"{tag}: 'featured' must be true or false")
        if d.get("featured") and d.get("expected"):
            errors.append(f"{tag}: watchlist (expected) deals can't be featured")
        key = (d.get("vendor"), d.get("offer"))
        if key in seen:
            errors.append(f"{tag}: duplicate vendor+offer")
        seen.add(key)
    return errors


# ───────────────────────────── rendering ─────────────────────────────
def render(deals: list[dict], today: dt.date) -> str:
    out: list[str] = []
    rows = [(status(d, today), d) for d in deals]
    live = [r for r in rows if r[0][0] in ("0", "1")]
    soon = [r for r in rows if r[0][0] == "2"]
    watch = [r for r in rows if r[0][0] == "4"]
    expired = [r for r in rows if r[0][0] == "5"]

    out.append(
        f"**{len(live)} live** · **{len(soon)} upcoming** · **{len(watch)} not announced yet** · "
        f"{len(expired)} expired — _statuses auto-refreshed {today:%b %d, %Y}_\n"
    )

    featured = sorted(
        (r for r in rows if r[1].get("featured") and r[0][0] in ("0", "1", "2")),
        key=lambda r: (r[0][0], r[1]["vendor"].lower()),
    )
    if featured:
        out.append("## ⭐ Featured deals\n")
        out.append("> Hand-picked by the maintainer: the best value right now.\n")
        out.append("| Vendor | Deal | Code | Ends | Status |")
        out.append("|---|---|---|---|---|")
        for (_, label), d in featured:
            certs = f"<br><sub>{', '.join(d['certs'])}</sub>" if d.get("certs") else ""
            note = f"<br><sub>⭐ {cell(d['featured_note'])}</sub>" if d.get("featured_note") else ""
            code = f"`{cell(d['code'])}`" if d.get("code") else "_auto_"
            out.append(
                f"| **[{cell(d['vendor'])}]({d['url']})**{certs} | {cell(d['discount'])}{note} "
                f"| {code} | {fmt_date(d.get('ends'))} | {label} |"
            )
        out.append("")

    ending = sorted(
        (r for r in live if as_date(r[1].get("ends"))),
        key=lambda r: as_date(r[1]["ends"]),
    )[:5]
    if ending:
        out.append("### ⏰ Ending next\n")
        for (_, label), d in ending:
            code = f" — code `{d['code']}`" if d.get("code") else ""
            out.append(f"- **[{d['vendor']}]({d['url']})** · {d['discount']}{code} · ends {fmt_date(d['ends'])}")
        out.append("")

    for cat, title in CATEGORIES.items():
        items = sorted(
            (r for r in rows if r[1]["category"] == cat and r[0][0] not in ("4", "5")),
            key=lambda r: (r[0][0], r[1]["vendor"].lower()),
        )
        if not items:
            continue
        out.append(f"## {title}\n")
        out.append("| Status | Vendor | Offer | Discount | Code | Ends | Verified |")
        out.append("|---|---|---|---|---|---|---|")
        for (_, label), d in items:
            certs = f"<br><sub>{', '.join(d['certs'])}</sub>" if d.get("certs") else ""
            note = f"<br><sub>ℹ️ {cell(d['notes'])}</sub>" if d.get("notes") else ""
            code = f"`{cell(d['code'])}`" if d.get("code") else ("—" if d["discount"] == "TBA" else "_auto_")
            verified = "✔️ vendor" if d["source"] == "vendor" else "⚠️ unconfirmed"
            out.append(
                f"| {label} | **[{cell(d['vendor'])}]({d['url']})** | {cell(d['offer'])}{certs}{note} "
                f"| {cell(d['discount'])} | {code} | {fmt_date(d.get('ends'))} | {verified} |"
            )
        out.append("")

    if watch:
        out.append("## 👀 Watchlist — not announced yet\n")
        out.append("These vendors usually run a Black Friday promo but haven't announced 2026 terms. "
                   "Open an issue / PR the moment they do.\n")
        out.append("| Vendor | Certs | What happened in 2025 | Notes |")
        out.append("|---|---|---|---|")
        for _, d in sorted(watch, key=lambda r: r[1]["vendor"].lower()):
            out.append(
                f"| **[{cell(d['vendor'])}]({d['url']})** | {cell(', '.join(d.get('certs') or [])) or '—'} "
                f"| {cell(d.get('last_year')) or '—'} | {cell(d.get('notes')) or '—'} |"
            )
        out.append("")

    if expired:
        out.append("<details><summary>❌ Expired deals</summary>\n")
        out.append("| Vendor | Offer | Discount | Ended |")
        out.append("|---|---|---|---|")
        for _, d in sorted(expired, key=lambda r: r[1]["vendor"].lower()):
            out.append(f"| [{cell(d['vendor'])}]({d['url']}) | {cell(d['offer'])} | {cell(d['discount'])} | {fmt_date(d['ends'])} |")
        out.append("\n</details>\n")

    return "\n".join(out)


def export(deals: list[dict], today: dt.date) -> None:
    clean = []
    for d in deals:
        row = {k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in d.items()}
        row["status"] = status(d, today)[1]
        clean.append(row)
    (ROOT / "data" / "deals.json").write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n")
    fields = ["status", "vendor", "category", "offer", "certs", "discount", "code",
              "starts", "ends", "url", "source", "source_url", "featured", "featured_note", "last_year", "notes", "last_checked"]
    with open(ROOT / "data" / "deals.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in clean:
            row = dict(row)
            row["certs"] = "; ".join(row.get("certs") or [])
            w.writerow(row)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="validate only")
    ap.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    args = ap.parse_args()

    deals = (yaml.safe_load(DATA.read_text()) or {}).get("deals") or []
    errors = validate(deals)
    if errors:
        print("❌ data/deals.yml has problems:\n  - " + "\n  - ".join(errors), file=sys.stderr)
        return 1
    print(f"✅ {len(deals)} deals valid")
    if args.check:
        return 0

    readme = README.read_text()
    if START not in readme or END not in readme:
        print(f"README.md is missing the {START} / {END} markers", file=sys.stderr)
        return 1
    head, rest = readme.split(START, 1)
    _, tail = rest.split(END, 1)
    README.write_text(f"{head}{START}\n{render(deals, args.today)}\n{END}{tail}")
    export(deals, args.today)
    print("📝 README.md, data/deals.json, data/deals.csv regenerated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
