"""Zero-argument factory for run_pilot: the data layer's fact checker over the pilot anchors and store."""
from pathlib import Path

from data_layer.facts import Fact
from data_layer.harness_adapter import DataLayerFactChecker, load_anchors
from harness.corpus import Corpus
from harness.injection import flagged_docs

HERE = Path(__file__).parent


def make() -> DataLayerFactChecker:
    inputs = HERE / "harness_inputs"
    facts = [Fact.model_validate_json(l) for l in (inputs / "store_facts.jsonl").read_text().splitlines() if l.strip()]
    corpus = Corpus.load(HERE / "corpus" / "pilot-v1")
    return DataLayerFactChecker(load_anchors(inputs / "anchors.jsonl"), facts, flagged_docs=flagged_docs(corpus))
