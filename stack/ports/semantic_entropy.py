"""Dependency-free port of the semantic-entropy core from jlko/semantic_uncertainty.

Source: https://github.com/jlko/semantic_uncertainty
        commit a8d9aa8cecd5f3bec09b19ae38ab13552e0846f4,
        semantic_uncertainty/uncertainty/uncertainty_measures/semantic_entropy.py
Licence: The Clear BSD License, Copyright (c) 2023 (see LICENSE.semantic_uncertainty in this folder).

Why a port: the upstream module imports torch, transformers and wandb at import time and
loads microsoft/deberta-v2-xlarge-mnli from huggingface.co. The
clustering and entropy functions below are copied with the logic unchanged; the entailment
model is injected as any callable `entails(a, b) -> 0 | 1 | 2` (0 contradiction, 1 neutral,
2 entailment), so the harness can pass a mock offline and a DeBERTa/LLM judge on lab infra.

For API models that return no token log-probs (e.g. Anthropic), use the discrete estimator
`cluster_assignment_entropy` (Farquhar et al. 2024, "discrete semantic entropy").
"""
from __future__ import annotations

from typing import Callable, Sequence

import numpy as np

Entails = Callable[[str, str], int]


def get_semantic_ids(strings_list: Sequence[str], entails: Entails, strict_entailment: bool = False) -> list[int]:
    """Group predictions into clusters of bidirectional entailment (upstream get_semantic_ids)."""

    def are_equivalent(t1: str, t2: str) -> bool:
        i1, i2 = entails(t1, t2), entails(t2, t1)
        assert i1 in (0, 1, 2) and i2 in (0, 1, 2)
        if strict_entailment:
            return i1 == 2 and i2 == 2
        return (0 not in (i1, i2)) and [i1, i2] != [1, 1]

    ids = [-1] * len(strings_list)
    next_id = 0
    for i, s1 in enumerate(strings_list):
        if ids[i] == -1:
            ids[i] = next_id
            for j in range(i + 1, len(strings_list)):
                if are_equivalent(s1, strings_list[j]):
                    ids[j] = next_id
            next_id += 1
    return ids


def logsumexp_by_id(semantic_ids: Sequence[int], log_likelihoods: Sequence[float]) -> list[float]:
    """Normalised log-probability mass per semantic cluster (upstream agg='sum_normalized')."""
    ll = np.asarray(log_likelihoods, dtype=float)
    norm = np.log(np.sum(np.exp(ll)))
    out = []
    for uid in sorted(set(semantic_ids)):
        idx = [k for k, x in enumerate(semantic_ids) if x == uid]
        out.append(float(np.log(np.sum(np.exp(ll[idx] - norm)))))
    return out


def predictive_entropy_rao(log_probs: Sequence[float]) -> float:
    lp = np.asarray(log_probs, dtype=float)
    return float(-np.sum(np.exp(lp) * lp))


def cluster_assignment_entropy(semantic_ids: Sequence[int]) -> float:
    """Discrete semantic entropy: entropy of the empirical cluster distribution."""
    ids = np.asarray(semantic_ids)
    _, counts = np.unique(ids, return_counts=True)
    p = counts / counts.sum()
    return float(-(p * np.log(p)).sum())
