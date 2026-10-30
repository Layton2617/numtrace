# Running models on NumTrace

`step1/run.py` runs one model on one batch. It splits the batch into shards, runs each shard as a separate
`harness.run_pilot` process, merges the results into one report per question and scores them with
`harness.rescore`. `status` and `rescore` never call a model.

The settings used in the paper are in `step1/configs/step1_cli.toml`: BM25 with k = 5, at most 6 retrieval queries
per question, no code tool, Claude Opus 5.5 through the Claude Code CLI (effort medium), GPT-6 Astra through the
Codex CLI (effort medium) and DeepSeek-V4.1-Flash through its API (effort high). `step1/configs/step1_api.toml` sends
all three through their APIs with the same retrieval and agent settings.

You need the corpus snapshot in `data/step1/corpus/step1-v2` first (see the top-level README).

## Commands

Try one question before a full batch:

```bash
python -m step1.run run --model opus-cli --batch statements --shards 4 --out harness/runs/step1 --limit 1
python -m step1.run run --model opus-cli --batch statements --shards 4 --out harness/runs/step1
```

DeepSeek reads its key from `DEEPSEEK_API_KEY`. With several keys, put each in its own environment variable and pass
their names; shard i uses the i-th key. Keys are passed only to the shard processes and are not written to any file.

```bash
python -m step1.run run --model deepseek --batch 13f --shards 3 --out harness/runs/step1 --key-env DS_KEY1,DS_KEY2,DS_KEY3
```

API route (keys from `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`):

```bash
python -m step1.run run --config step1/configs/step1_api.toml --model opus-api --batch nqdc --out harness/runs/step1
```

To add a model, add a `[models.<name>]` section to a config and, if needed, an adapter in `harness/models.py`.

Progress and scoring:

```bash
python -m step1.run status --out harness/runs/step1
python -m step1.run rescore --out harness/runs/step1
```

## Resuming

A question counts as done when an earlier run wrote its trajectory for the same question text, snapshot id and
agent config hash. Each `run` only runs questions that are not done, so after an interruption or a failed question,
run the same command again. If the snapshot or the config changes, old results no longer count; nothing is deleted.
Do not start two runs for the same model and batch at once.

## Output

Under `harness/runs/step1/<model>/<batch>/`:

- `runs/<time>-s<i>/`: shard i of one invocation (`questions.jsonl`, `run.log`, the run directory of `run_pilot`)
- `trajectories.jsonl`, `claims.jsonl`, `slots.jsonl`, `reports.jsonl`: merged at run time, scored by the scorer of
  that time (superseded by `rescored/`)
- `merge.json`: which run each question came from, missing questions, model versions
- `rescored/`, `rescore.log`: scores from the current scorer. These are the numbers to report.

The released runs on Hugging Face do not include the shards' `run.log` files.
- `config.toml`: the config with the batch filled in

After a run, check that the last line of the output is `DONE: <model> <batch> n/n requested`, that `merge.json`
lists one model version and no missing questions, and that no `run.log` contains `used tools` (the CLI agents must
answer from the retrieved text only).

## Memory

The snapshot has 41,726 chunks and a 559 MB `docs.jsonl`. By default (`--index mmap`) the BM25 statistics are built
once under `harness/runs/step1/bm25/<snapshot id>/` (about 20 seconds, 140 MB) and memory-mapped by every shard, so the
retrieval part of a shard takes about 70 MB, plus the fact checker. Retrieval results are identical to `rank_bm25`
(`step1/tests/test_run.py`). `--index harness` loads the full snapshot in every shard (5.5 to 6.6 GB each); use it
only with one shard.
