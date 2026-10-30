"""One-question smoke test for real models (CLI or API): does every stage come back as parseable JSON,
how many tokens, did the CLI use tools, which model/CLI version answered. Nothing is scored.

    python -m harness.cli_smoke --config pilot/pilot_cli.toml --models opus-cli,gpt-cli [--question TGT-1]

Writes the trajectory to harness/runs/smoke/smoke-<model>-<question>.json so the raw outputs can be read.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent import AgentConfig, DeepResearchAgent, parse_json
from .run_pilot import build_models, load_config, load_corpus, load_questions
from .schema import RetrievalConfig


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--models", required=True)
    ap.add_argument("--question", default=None, help="question id (default: the first one)")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    rc = cfg.get("run", {})
    corpus = load_corpus(rc["snapshot"])
    qs = load_questions(rc["questions"])
    q = next(x for x in qs if x.id == a.question) if a.question else qs[0]
    retrieval = RetrievalConfig(**cfg.get("retrieval", {}))
    ok_all = True
    for model in build_models(cfg, [m.strip() for m in a.models.split(",") if m.strip()]):
        agent_cfg = AgentConfig(model=model.name, retrieval=retrieval, **cfg.get("agent", {}))
        print(f"\n=== {model.name} on {q.id}")
        try:
            traj = DeepResearchAgent(model, corpus, agent_cfg).run(q)
        except Exception as e:  # report and go on to the next model
            ok_all = False
            print(f"FAILED: {type(e).__name__}: {e}")
            continue
        for st in traj.steps:
            if st.raw_model_output is None:
                continue
            if st.stage.value == "synthesis":      # the report stage asks for prose, not JSON
                parsed = "text"
            else:
                try:
                    parse_json(st.raw_model_output)
                    parsed = "json ok"
                except Exception:
                    parsed = "NOT JSON"
                    ok_all = False
            u = st.usage
            print(f"  {st.stage.value:<12} {parsed:<9} in={u.prompt_tokens if u else '-':>7} "
                  f"out={u.completion_tokens if u else '-':>6}")
        print(f"  model_version: {traj.model_version}")
        print(f"  total tokens in/out: {traj.usage.prompt_tokens}/{traj.usage.completion_tokens}, "
              f"API-equivalent cost ${traj.usage.cost_usd:.4f}")
        for entry in getattr(model, "call_log", []):
            if entry.get("tools_used") or entry.get("num_turns", 1) > 1:
                ok_all = False
                print(f"  WARNING tool use: {entry}")
        print(f"  report: {traj.report[:300]!r}")
        out = Path(rc.get("out_dir", "harness/runs")) / "smoke" / f"smoke-{model.name}-{q.id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(traj.model_dump_json(indent=1))
        print(f"  trajectory: {out}")
    print("\nSMOKE OK" if ok_all else "\nSMOKE HAD PROBLEMS (see above)")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
