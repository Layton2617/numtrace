import math

import numpy as np
import pytest

from harness.monitors import (ConceptCheckMonitor, LLMJudgeMonitor, MonitorInput, NLIMonitor,
                              SelfConsistencyMonitor, SemanticEntropyMonitor, auprc, auroc, cluster_bootstrap_ci,
                              evaluate_monitor, lookup_from_stub, mock_judge, mock_sampler, recall_at_fpr,
                              threshold_at_fpr, transfer_threshold)
from harness.scoring import StubFactChecker
from harness.schema import Verdict


def test_auroc_auprc_recall():
    y = [0, 0, 1, 1]
    assert auroc(y, [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert auroc(y, [0.9, 0.8, 0.2, 0.1]) == 0.0
    assert auroc(y, [0.5, 0.5, 0.5, 0.5]) == 0.5
    assert auprc(y, [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert abs(auprc([1, 0, 1, 0], [0.9, 0.8, 0.7, 0.1]) - (0.5 * 1 + 0.5 * 2 / 3)) < 1e-12
    rng = np.random.default_rng(0)
    yy = rng.integers(0, 2, 400)
    assert abs(auroc(yy, rng.random(400)) - 0.5) < 0.1
    y20 = [0] * 20 + [1] * 5
    s20 = list(np.linspace(0, 0.5, 20)) + [0.45, 0.6, 0.7, 0.8, 0.9]
    assert threshold_at_fpr(y20, s20, 0.05) == pytest.approx(0.5)
    assert recall_at_fpr(y20, s20, 0.05) == 0.8


def test_cluster_bootstrap_and_transfer():
    rng = np.random.default_rng(1)
    y = rng.integers(0, 2, 200)
    s = y * 0.5 + rng.random(200) * 0.7
    clusters = [f"e{i % 10}" for i in range(200)]
    p, lo, hi = cluster_bootstrap_ci(auroc, y, s, clusters, n_boot=200)
    assert lo <= p <= hi and 0.5 < p <= 1
    rep = evaluate_monitor(y, s, clusters, n_boot=100)
    assert set(rep) == {"auroc", "auprc", "recall@5%fpr"}
    t = transfer_threshold(y, s, y, s)
    assert t["fpr"] <= 0.05 + 1e-12


def _inputs(run, corpus, questions, gold, walkthrough_mistakes):
    stub = StubFactChecker(gold)
    xs, labels, clusters = [], [], []
    for qid, q in questions.items():
        for mk in ([], walkthrough_mistakes.get(qid, [])):
            traj, scored, _ = run(corpus, q, gold, mk)
            for s in scored:
                xs.append(MonitorInput(claim=s.claim, question=q, trajectory=traj, fact_evidence=s.fact_check))
                labels.append(int(s.verdicts.binding == Verdict.fail))
                clusters.append(q.entity)
    return stub, xs, labels, clusters


def test_monitors_run_offline_and_tier2_concept_check_catches_binding_failures(run, corpus, questions, gold,
                                                                             walkthrough_mistakes):
    stub, xs, y, clusters = _inputs(run, corpus, questions, gold, walkthrough_mistakes)
    assert sum(y) == 4
    concept = ConceptCheckMonitor(lookup_from_stub(stub))
    nli = NLIMonitor()
    judge1, judge2 = LLMJudgeMonitor(mock_judge, tier=1), LLMJudgeMonitor(mock_judge, tier=2)
    scores = {m.name: [m.score(x) for x in xs] for m in (concept, nli, judge1, judge2)}
    for v in scores.values():
        assert all(0.0 <= s <= 1.0 for s in v)
    assert auroc(y, scores["concept_check"]) == 1.0
    assert auroc(y, scores[nli.name]) < 1.0
    assert "FACT STORE" in judge2.prompt(xs[0]) and "FACT STORE" not in judge1.prompt(xs[0])


def test_sampling_monitors(corpus, questions):
    q = questions["q-stmt-acme-ltd"]
    sampler = mock_sampler(corpus, noise=0.5)
    sc = SelfConsistencyMonitor(sampler, k=6)
    se = SemanticEntropyMonitor(sampler, k=6)
    claims = sampler(q, 0)
    x = MonitorInput(claim=claims[0], question=q, trajectory=None)
    s1, s2 = sc.score(x), se.score(x)
    assert 0.0 <= s1 <= 1.0 and 0.0 <= s2 <= 1.0
    assert sc.score(x) == s1
    stable = SelfConsistencyMonitor(mock_sampler(corpus, noise=0.0), k=3)
    assert stable.score(x) in (0.0, 1.0)
    assert not math.isnan(s2)
