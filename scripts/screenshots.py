"""Click through every screen in a real (headless) Chrome and save screenshots to docs/screenshots/.

    python scripts/screenshots.py --base http://localhost:8000 [--dark] [--mobile]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--dark", action="store_true")
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    suffix = ("-dark" if a.dark else "") + ("-mobile" if a.mobile else "")
    errors: list[str] = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        vp = {"width": 390, "height": 844} if a.mobile else {"width": 1440, "height": 900}
        ctx = b.new_context(viewport=vp, color_scheme="dark" if a.dark else "light", device_scale_factor=1)
        page = ctx.new_page()
        page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        def shot(name: str) -> None:
            if a.only and a.only not in name:
                return
            page.wait_for_timeout(700)
            page.screenshot(path=str(OUT / f"{name}{suffix}.png"), full_page=False)
            print("saved", name + suffix)

        page.goto(a.base + "/")
        page.wait_for_selector("text=Continue as Asha Rao")
        shot("01-login")
        page.click("text=Continue as Asha Rao")
        page.wait_for_selector("text=Write path")

        if a.mobile:
            pass
        page.fill("textarea[aria-label=Message]", "I avoid seafood, but I love sushi.")
        page.keyboard.press("Enter")
        page.wait_for_selector("text=Conflict rules")
        page.wait_for_timeout(900)
        shot("02-chat-write-path")
        page.fill("textarea[aria-label=Message]", "Any dinner ideas?")
        page.keyboard.press("Enter")
        page.wait_for_selector("text=Memory block sent to the model")
        page.click("text=Why? >> nth=0")
        page.wait_for_timeout(400)
        page.get_by_role("heading", name="Read path").scroll_into_view_if_needed()
        shot("03-chat-read-path")

        page.goto(a.base + "/memory")
        page.wait_for_selector("canvas")
        page.wait_for_timeout(1500)
        shot("04-memory-graph")
        page.goto(a.base + "/timeline")
        page.wait_for_selector(".lane")
        page.locator(".seg-v.retired").first.click()
        shot("05-timeline")
        page.goto(a.base + "/scenarios")
        page.click("text=A later, broader statement >> xpath=../.. >> button:has-text('Play')")
        page.wait_for_selector("text=Message 1 of")
        page.click("text=Next message")
        page.wait_for_timeout(600)
        shot("06-scenario-r3")
        page.goto(a.base + "/compare")
        page.click("text=Run both")
        page.wait_for_selector("text=only here")
        shot("07-compare")
        page.goto(a.base + "/ontology")
        page.wait_for_selector(".tree")
        shot("08-ontology")
        page.click("text=Review queue")
        page.wait_for_timeout(800)
        shot("09-ontology-review")
        page.goto(a.base + "/evaluation")
        page.wait_for_selector("text=Clean retrieval")
        page.wait_for_timeout(900)
        shot("10-evaluation")
        page.click("role=tab[name='p/q model']")
        page.wait_for_timeout(900)
        shot("11-pq-model")
        page.click("role=tab[name='Failure browser']")
        page.wait_for_timeout(900)
        shot("12-failures")
        page.goto(a.base + "/data")
        page.click("text=Run query")
        page.wait_for_selector("text=row(s) from a graph")
        page.click("text=Validate my memory")
        page.wait_for_selector("text=triples checked")
        shot("13-data")
        b.close()
    if errors:
        print("\nBROWSER ERRORS:")
        for e in errors:
            print(" ", e)
    else:
        print("no browser errors")


if __name__ == "__main__":
    main()
