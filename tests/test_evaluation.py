"""The evaluation harness: metric sanity, LLM-system plumbing (with a scripted fake model), stats."""

import json
from datetime import datetime

import pytest

from ontomem.core.llm import LLMError, ReplayLLM, ScriptedLLM
from ontomem.core.ontology import default_ontology
from ontomem.evaluation.analytic import p_probe_stale
from ontomem.evaluation.metrics import ndcg_at_k
from ontomem.evaluation.runner import evaluate, prepare, run_system
from ontomem.evaluation.stats import holm, mcnemar
from ontomem.evaluation.systems import Item, append_only, oracle, sim_llm
from ontomem.prefrev.generator import Generator, extended_ontology


@pytest.fixture(scope="module")
def small():
    gen = Generator(seed=3)
    eps = [gen.episode(i, "test") for i in range(12)]
    onto = default_ontology()
    gold = extended_ontology(onto)
    return eps, onto, gold, prepare(eps, "A", onto, gold)


def test_oracle_is_perfect_and_append_only_never_merges(small):
    eps, onto, gold, streams = small
    o = run_system(oracle(), streams, onto, gold, "A")
    assert all(p.clean for p in o["probes"])
    assert all(p.ndcg5 == 1.0 and not p.harmful5 for p in o["probes"])
    a = run_system(append_only(), streams, onto, gold, "A", downstream=False)
    assert all(w.decision == "new" for w in a["writes"])
    revisions = [w for w in a["writes"] if w.is_revision and w.eligible]
    assert revisions and not any(w.correct_merge for w in revisions)


def test_sim_rates_are_respected(small):
    eps, onto, gold, streams = small
    r = run_system(sim_llm(0.0, 0.0), streams, onto, gold, "A", downstream=False)
    assert not any(w.false_merge for w in r["writes"])
    r = run_system(sim_llm(1.0, 0.0), streams, onto, gold, "A", downstream=False)
    rev = [w for w in r["writes"] if w.is_revision and w.eligible]
    assert rev and not any(w.correct_merge for w in rev)


def test_full_pipeline_ranks_ontomem_above_baselines():
    s = evaluate("A", "test", ["S0", "S1", "S3", "S4"], limit=25, bootstrap=50)
    rows = {r["system"]: r for r in s["table"]}
    assert rows["S4-oracle"]["clean_retrieval"] == 1.0
    assert rows["S3-OntoMem"]["clean_retrieval"] > rows["S1-rule-surface"]["clean_retrieval"]
    assert rows["S3-OntoMem"]["clean_retrieval"] > rows["S0-append-only"]["clean_retrieval"]
    assert rows["S3-OntoMem"]["false_merge_rate_q"] <= rows["S1-rule-surface"]["false_merge_rate_q"]


def test_analytic_model_basics():
    assert p_probe_stale({"k": ["likes"]}, 0.5, 0.5) == 0.0  # never revised -> never stale
    assert p_probe_stale({"k": ["likes", "dislikes"]}, 0.0, 0.0) == 0.0
    assert p_probe_stale({"k": ["likes", "dislikes"]}, 1.0, 0.0) == 1.0
    assert abs(p_probe_stale({"k": ["likes", "dislikes"]}, 0.2, 0.0) - 0.2) < 1e-9


def test_metric_helpers():
    assert ndcg_at_k(["a", "b"], {"a": 2, "b": 1}) == 1.0
    assert ndcg_at_k(["b", "a"], {"a": 2, "b": 1}) < 1.0
    t = mcnemar([True] * 30 + [False] * 5, [False] * 30 + [True] * 5)
    assert t["p_value"] < 0.001
    adj = holm({"x": 0.01, "y": 0.04, "z": 0.5})
    assert adj["x"] == pytest.approx(0.03) and adj["y"] == pytest.approx(0.08) and adj["z"] == 0.5


def _item(i, surface, pol, gk, concept):
    return Item(item_id=f"t{i}#0", turn_id=f"t{i}", ts=datetime(2026, 1, i + 1), surface=surface, polarity=pol,
                who="self", aspect="general", intent="standing", context="any", reason="unspecified", strength=0.8,
                text=f"I {pol} {surface}", concept=concept, gold_key=gk, gold_polarity=pol, gold_domain="Food")


def test_mem0_style_system_with_scripted_llm():
    from ontomem.evaluation.llm_systems import mem0_system

    def brain(prompt, _messages):
        # update when the new fact mentions spicy and memory [0] does too
        if "NEW FACT" in prompt and "spicy" in prompt.split("NEW FACT")[1] and "[0]" in prompt and "spicy" in prompt.split("[0]")[1].split("\n")[0]:
            return {"action": "UPDATE", "id": 0}
        return {"action": "ADD", "id": None}

    s = mem0_system(ScriptedLLM(brain, name="fake"))
    s.write(_item(0, "spicy food", "likes", "k1", "SpicyFood"))
    s.write(_item(1, "sushi", "likes", "k2", "Sushi"))
    rec = s.write(_item(2, "spicy dishes", "avoids", "k1", "SpicyFood"))
    assert rec.decision == "supersede"
    assert {n.surface for n in s.active_nodes()} == {"spicy dishes", "sushi"}
    assert s.usage()["calls"] == 2


def test_ontology_constrained_system_with_scripted_llm():
    from ontomem.evaluation.llm_systems import OntologyConstrainedSystem

    s = OntologyConstrainedSystem(default_ontology(), ScriptedLLM(lambda p, m: {"concept": "SpicyFood"}, name="fake"))
    s.write(_item(0, "chilli-laden food", "likes", "k1", "x"))
    s.write(_item(1, "fiery curries", "avoids", "k1", "x"))
    act = s.active_nodes()
    assert len(act) == 1 and act[0].key.what == "SpicyFood" and act[0].polarity.value == "avoids"


def test_replay_llm_never_invents():
    r = ReplayLLM(responses={})
    with pytest.raises(LLMError):
        r.chat([{"role": "user", "content": "hi"}])


def test_results_are_json_serialisable(tmp_path):
    evaluate("B", "test", ["S3", "S4"], limit=5, bootstrap=0, out=tmp_path)
    assert json.loads((tmp_path / "summary.json").read_text())["meta"]["mode"] == "B"
    assert (tmp_path / "table.tex").read_text().startswith("\\begin{tabular}")
