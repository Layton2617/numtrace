#!/usr/bin/env bash
# Idempotent install of the NumTrace stack into the current Python (Python 3.11).
# Re-running is safe: pip skips satisfied pins, clones are skipped when already at the pinned commit.
set -euo pipefail
cd "$(dirname "$0")"
PIP_FLAGS="${PIP_FLAGS:---break-system-packages}"

python3 -m pip install -q $PIP_FLAGS -r requirements.txt

mkdir -p vendor
clone_pinned () {   # name url sha
  local name=$1 url=$2 sha=$3
  if [ -d "vendor/$name/.git" ] && [ "$(git -C "vendor/$name" rev-parse HEAD)" = "$sha" ]; then
    echo "vendor/$name already at $sha"; return
  fi
  rm -rf "vendor/$name"; mkdir -p "vendor/$name"
  git -C "vendor/$name" init -q
  git -C "vendor/$name" remote add origin "$url"
  git -C "vendor/$name" fetch -q --depth 1 origin "$sha"
  git -C "vendor/$name" checkout -q FETCH_HEAD
  echo "vendor/$name -> $sha"
}
# edgartools: source of real SEC fixtures (tests/cassettes, tests/fixtures, data/) ~2 GB checkout
clone_pinned edgartools          https://github.com/dgunning/edgartools.git       e46440efe29c97ac4c70903e3382d143c5bd00b4
clone_pinned summac              https://github.com/tingofurro/summac.git         c1f3da93cd074c24d8033eb27a88b5a7cc5c08fa
clone_pinned semantic_uncertainty https://github.com/jlko/semantic_uncertainty.git a8d9aa8cecd5f3bec09b19ae38ab13552e0846f4

# Extract real SEC responses (13F-HR, DEF 14A, companyfacts) from edgartools' VCR cassettes
if [ ! -f fixtures/sec/index.json ]; then
  mkdir -p fixtures/sec
  python3 tools/extract_cassettes.py --write > fixtures/sec/index.tsv
fi
echo "install ok — run: python3 smoke_test.py"
