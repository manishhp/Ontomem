"""Report figures (PNG + SVG) from the result folders written by the runner and the sweep.

    python -m ontomem.evaluation.figures --results results --out results/figures

Style: one highlighted series (OntoMem, blue) against neutral baselines; thin marks,
recessive grids, direct labels, no dual axes. Colours from a validated CVD-safe palette.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BLUE, BLUE_DARK = "#2a78d6", "#1c5cab"
GREY, GREY_LIGHT = "#8a8985", "#c9c8c3"
INK, INK_2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
SEQ = ["#ffffff", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
MAIN = ["S0-append-only", "S1-rule-surface", "S5-learned", "S3-OntoMem", "S4-oracle"]
LABEL = {"S0-append-only": "S0 append-only", "S1-rule-surface": "S1 rule (surface key)", "S5-learned": "S5 learned keyer",
         "S3-OntoMem": "S3 OntoMem (ours)", "S4-oracle": "S4 oracle keys"}


def _style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": INK_2,
        "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlecolor": INK, "axes.titlelocation": "left",
        "xtick.color": INK_2, "ytick.color": INK_2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False, "axes.axisbelow": True, "figure.dpi": 150,
        "savefig.bbox": "tight", "legend.frameon": False,
    })


def _save(fig, out: Path, name: str) -> None:
    fig.savefig(out / f"{name}.png")
    fig.savefig(out / f"{name}.svg")
    plt.close(fig)


def _rows(summary: dict) -> dict[str, dict]:
    return {r["system"]: r for r in summary["table"]}


def fig_main(res: Path, out: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.4), sharey=True)
    for ax, mode in zip(axes, ["A", "B"]):
        p = res / f"mode{mode}" / "summary.json"
        if not p.exists():
            continue
        rows = _rows(json.loads(p.read_text()))
        names = [n for n in MAIN if n in rows]
        vals = [100 * rows[n]["clean_retrieval"] for n in names]
        cis = [rows[n].get("clean_retrieval_ci") or [None, None] for n in names]
        colors = [BLUE if n.startswith("S3") else (GREY_LIGHT if n.startswith("S4") else GREY) for n in names]
        y = range(len(names))
        ax.barh(y, vals, color=colors, height=0.55, edgecolor="white", linewidth=2)
        for i, (v, ci) in enumerate(zip(vals, cis)):
            end = v
            if ci[0] is not None:
                ax.plot([100 * ci[0], 100 * ci[1]], [i, i], color=INK, lw=1.2)
                end = max(v, 100 * ci[1])
            ax.text(end + 1.5, i, f"{v:.1f}%", va="center", color=INK, fontsize=9)
        ax.set_yticks(list(y), [LABEL[n] for n in names])
        ax.set_xlim(0, 112)
        ax.set_xlabel("clean retrieval (% of probes), 95% cluster-bootstrap CI")
        ax.set_title(f"Mode {mode}: " + ("gold extraction (isolates keying)" if mode == "A" else "end-to-end from raw text"))
        ax.grid(axis="y", visible=False)
    _save(fig, out, "fig1_clean_retrieval")


def fig_merge_errors(res: Path, out: Path) -> None:
    p = res / "modeA" / "summary.json"
    if not p.exists():
        return
    rows = _rows(json.loads(p.read_text()))
    names = [n for n in MAIN if n in rows and not n.startswith("S4")]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.0), sharey=True)
    for ax, (metric, title) in zip(axes, [("missed_merge_rate_p", "Missed merges p (% of revisions)"),
                                          ("false_merge_rate_q", "False merges q (% of writes)"),
                                          ("stale_exposure", "Stale exposure (% of probes)")]):
        vals = [100 * (rows[n][metric] or 0) for n in names]
        y = range(len(names))
        ax.barh(y, vals, color=[BLUE if n.startswith("S3") else GREY for n in names], height=0.55)
        for i, v in enumerate(vals):
            ax.text(v + max(vals) * 0.02, i, f"{v:.1f}", va="center", fontsize=9, color=INK)
        ax.set_yticks(list(y), [LABEL[n] for n in names])
        ax.set_xlim(0, max(vals) * 1.2 + 0.5)
        ax.set_title(title)
        ax.grid(axis="y", visible=False)
    _save(fig, out, "fig2_merge_errors")


def fig_ablation(res: Path, out: Path) -> None:
    p = res / "modeA" / "summary.json"
    if not p.exists():
        return
    rows = _rows(json.loads(p.read_text()))
    full = rows["S3-OntoMem"]
    abl = [n for n in rows if n.startswith("S3 -")]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.2), sharey=True)
    for ax, (metric, title, sign) in zip(axes, [("clean_retrieval", "Clean retrieval lost (points)", -1),
                                                ("harmful5", "Harmful suggestions added (points)", 1)]):
        vals = [round(sign * 100 * ((rows[n][metric] or 0) - (full[metric] or 0)), 1) + 0.0 for n in abl]
        y = range(len(abl))
        ax.barh(y, vals, color=BLUE, height=0.55)
        for i, v in enumerate(vals):
            ax.text(max(v, 0) + 0.3, i, f"{v:+.1f}", va="center", fontsize=9, color=INK)
        ax.set_yticks(list(y), [n.replace("S3 ", "remove ").replace("-", "") for n in abl])
        ax.axvline(0, color=INK_2, lw=1)
        ax.set_title(title)
        ax.set_xlim(min(0, min(vals)) - 0.5, max(vals) * 1.25 + 0.5)
        ax.grid(axis="y", visible=False)
    _save(fig, out, "fig3_ablation")


def fig_sweep(res: Path, out: Path) -> None:
    p = res / "sweep" / "sweep.json"
    if not p.exists():
        return
    rows = json.loads(p.read_text())
    qs = sorted({r["q"] for r in rows})
    fig, ax = plt.subplots(figsize=(6.4, 4))
    for color, q in zip(SERIES, qs):
        pts = sorted((r for r in rows if r["q"] == q), key=lambda r: r["p"])
        ps = [r["p"] for r in pts]
        ax.plot(ps, [100 * r["analytic"] for r in pts], color=color, lw=2)
        ax.plot(ps, [100 * r["simulated"] for r in pts], "o", color=color, ms=6, mec="white", mew=2)
    from matplotlib.lines import Line2D

    handles = [Line2D([], [], color=c, lw=2, marker="o", mec="white", mew=1.5, label=f"q = {q:.2f}") for c, q in zip(SERIES, qs)]
    ax.legend(handles=handles, title="false-merge rate", loc="upper left", fontsize=9, title_fontsize=9)
    ax.set_xlabel("missed-merge rate p")
    ax.set_ylabel("stale exposure (% of probes)")
    ax.set_xlim(-0.02, max(r["p"] for r in rows) + 0.03)
    ax.set_title("Analytic model (lines) vs simulation (dots)")
    _save(fig, out, "fig4_pq_sensitivity")


def fig_cases(res: Path, out: Path) -> None:
    p = res / "modeA" / "summary.json"
    if not p.exists():
        return
    s = json.loads(p.read_text())
    systems = [n for n in MAIN if n in s["by_case"] and not n.startswith("S4")]
    cases = ["replace", "reinforce", "exception", "broad_override", "intent", "context", "trap", "third_party", "price", "coverage"]
    grid = []
    for n in systems:
        row = []
        for c in cases:
            b = s["by_case"][n].get(c, {})
            q = b.get("false_merge_rate_q") or 0
            pm = b.get("missed_merge_rate_p") or 0
            row.append(100 * max(q, pm))
        grid.append(row)
    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list("seq", SEQ[1:])
    fig, ax = plt.subplots(figsize=(10, 2.6))
    im = ax.imshow(grid, cmap=cmap, aspect="auto", vmin=0, vmax=max(max(r) for r in grid) or 1)
    ax.set_xticks(range(len(cases)), [c.replace("_", " ") for c in cases], rotation=25, ha="right")
    ax.set_yticks(range(len(systems)), [LABEL[n] for n in systems])
    ax.grid(False)
    for i, row in enumerate(grid):
        for j, v in enumerate(row):
            ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=8, color="white" if v > 0.55 * im.get_clim()[1] else INK)
    ax.set_title("Worst of missed / false merge rate (%) by case type - Mode A")
    fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)
    _save(fig, out, "fig5_case_types")


def fig_stages(res: Path, out: Path) -> None:
    p = res / "modeB" / "summary.json"
    if not p.exists():
        return
    s = json.loads(p.read_text())
    st = s["error_stages"].get("S3-OntoMem", {})
    if not st:
        return
    items = sorted(st.items(), key=lambda kv: kv[1])
    fig, ax = plt.subplots(figsize=(6.4, 0.4 * len(items) + 1))
    ax.barh(range(len(items)), [v for _, v in items], color=BLUE, height=0.55)
    for i, (_, v) in enumerate(items):
        ax.text(v + 0.3, i, str(v), va="center", fontsize=9)
    ax.set_yticks(range(len(items)), [k for k, _ in items])
    ax.set_xlabel("merge errors attributed to stage (count)")
    ax.set_title("Where OntoMem's end-to-end errors come from (Mode B)")
    ax.grid(axis="y", visible=False)
    _save(fig, out, "fig6_error_stages")


def make_all(results: str | Path = "results", out: str | Path | None = None) -> list[str]:
    _style()
    res = Path(results)
    o = Path(out) if out else res / "figures"
    o.mkdir(parents=True, exist_ok=True)
    for f in (fig_main, fig_merge_errors, fig_ablation, fig_sweep, fig_cases, fig_stages):
        f(res, o)
    return sorted(str(p) for p in o.glob("*.png"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    for f in make_all(a.results, a.out):
        print(f)


if __name__ == "__main__":
    main()
