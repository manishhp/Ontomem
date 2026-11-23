"""Command-line entry point.

    ontomem serve                 start the API (+ built web app) on :8000
    ontomem chat                  talk to the memory in your terminal (offline)
    ontomem scenario sushi        replay a scripted scenario and print each rule that fires
    ontomem crosscheck            Python vs ASP differential test
    ontomem bench                 run the main experiments (Mode A and B)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime


def _print_trace(tr: dict) -> None:
    for c in tr["candidates"]:
        m = c["mapping"]
        if not c["accepted"]:
            print(f"   ✗ rejected by SHACL: {c['validation']}")
            continue
        rules = ", ".join(f"{f['rule']}: {f['message']}" for f in c["outcome"]["firings"])
        print(f"   • {c['raw']['who']} {c['raw']['polarity']} '{c['raw']['surface']}' → {' › '.join(m['path']) or m['concept']}"
              f"  [{m['method']}]\n     key {c['key']}\n     {rules}")
    for q in tr.get("clarifications", []):
        print(f"   ? {q['question']}")


def cmd_chat(_: argparse.Namespace) -> None:
    from .core.engine import OntoMemEngine
    from .core.events import EventStore

    eng = OntoMemEngine(store=EventStore())
    print("OntoMem chat - tell me what you like, ask for ideas, :mem to show memory, :q to quit.")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text in (":q", ":quit"):
            break
        if text == ":mem":
            for n in eng.memory("me").active():
                print("  ", n.key, n.polarity.value)
            continue
        tr = eng.observe("me", text, datetime.now().replace(microsecond=0))
        _print_trace(tr.to_dict())
        if text.endswith("?") or any(w in text.lower() for w in ("idea", "suggest", "recommend", "what should")):
            print("bot>", eng.ask("me", text)["reply"])


def cmd_scenario(a: argparse.Namespace) -> None:
    from importlib import resources

    from .core.engine import OntoMemEngine
    from .core.events import EventStore

    data = json.loads((resources.files("ontomem") / "data" / "scenarios.json").read_text())
    sc = next((s for s in data["scenarios"] if s["id"] == a.id), None)
    if sc is None:
        sys.exit("scenarios: " + ", ".join(s["id"] for s in data["scenarios"]))
    eng = OntoMemEngine(store=EventStore())
    print(f"== {sc['title']} ==\n{sc['summary']}\n")
    for t in sc["turns"]:
        ts = datetime.fromisoformat(t["ts"])
        print(f"[{ts.date()}] {t['text']}")
        tr = eng.observe("u", t["text"], ts)
        _print_trace(tr.to_dict())
        if t["text"].endswith("?"):
            print("   →", eng.ask("u", t["text"], now=ts)["reply"])
        print()


def main() -> None:
    ap = argparse.ArgumentParser(prog="ontomem", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--host", default="127.0.0.1")
    sub.add_parser("chat")
    sc = sub.add_parser("scenario")
    sc.add_argument("id")
    sub.add_parser("crosscheck")
    sub.add_parser("bench")
    a = ap.parse_args()
    if a.cmd == "serve":
        import uvicorn

        uvicorn.run("ontomem.backend.app:app", host=a.host, port=a.port)
    elif a.cmd == "chat":
        cmd_chat(a)
    elif a.cmd == "scenario":
        cmd_scenario(a)
    elif a.cmd == "crosscheck":
        from .core.asp.crosscheck import main as cc

        sys.argv = ["crosscheck", "--n", "2000"]
        cc()
    elif a.cmd == "bench":
        from .evaluation.runner import evaluate

        for mode in ("A", "B"):
            evaluate(mode, out=f"results/mode{mode}", progress=lambda i, n, name: print(f"[{i + 1}/{n}] {name}"))


if __name__ == "__main__":
    main()
