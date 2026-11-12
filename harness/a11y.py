"""
Accessibility scan: runs axe-core (WCAG 2.1 A/AA rules) on every page of a running web app.

    A11Y_BASE_URL=http://localhost:3010 A11Y_OWNER_EMAIL=... A11Y_OWNER_PASSWORD=... python harness/a11y.py

Needs `npm install` in apps/web (for axe-core) and a Playwright Chromium. Writes harness/reports/a11y.md.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
AXE = ROOT / "apps" / "web" / "node_modules" / "axe-core" / "axe.min.js"
REPORT = ROOT / "harness" / "reports" / "a11y.md"
PUBLIC = ["/", "/login", "/order-status"]
MERCHANT = ["/console", "/kitchen", "/onboarding"]


def scan(page, url: str) -> list[dict]:  # type: ignore[no-untyped-def]
    page.goto(url, wait_until="networkidle")
    page.wait_for_timeout(800)
    page.add_script_tag(path=str(AXE))
    result = page.evaluate(
        "async () => await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}})"
    )
    return result["violations"]


def main() -> int:
    base = os.environ.get("A11Y_BASE_URL", "http://localhost:3010").rstrip("/")
    rows: list[tuple[str, list[dict]]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        for path in PUBLIC:
            rows.append((path, scan(page, base + path)))
        email, password = os.environ.get("A11Y_OWNER_EMAIL"), os.environ.get("A11Y_OWNER_PASSWORD")
        if email and password:
            page.goto(base + "/login")
            page.fill("#email", email)
            page.fill("#password", password)
            page.click("button[type=submit]")
            page.wait_for_url(base + "/console", timeout=15000)
            for path in MERCHANT:
                rows.append((path, scan(page, base + path)))
        browser.close()

    total = sum(len(v) for _, v in rows)
    lines = ["# Accessibility scan (axe-core, WCAG 2.1 A/AA)", "",
             f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M UTC} against `{base}`. Automated rules catch only part of "
             "WCAG; keyboard and screen-reader passes are still manual.", "",
             "| Page | Violations | Rules |", "| --- | ---: | --- |"]
    for path, violations in rows:
        rules = ", ".join(f"`{v['id']}` ({v['impact']}, {len(v['nodes'])} nodes)" for v in violations) or "none"
        lines.append(f"| `{path}` | {len(violations)} | {rules} |")
    if total:
        lines += ["", "## Details", ""]
        for path, violations in rows:
            for v in violations:
                targets = "; ".join(" ".join(n["target"]) if isinstance(n["target"], list) else str(n["target"])
                                    for n in v["nodes"][:5])
                lines.append(f"- `{path}` **{v['id']}**: {v['help']} — {targets}")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({p: [v["id"] for v in vs] for p, vs in rows}, indent=1))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
