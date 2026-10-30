import subprocess
import sys
from pathlib import Path

import pytest

from data_layer import edgar_fetch

ROOT = Path(__file__).resolve().parents[2]


def test_cache_path_mirrors_url():
    p = edgar_fetch.cache_path("https://data.sec.gov/submissions/CIK0000027419.json")
    assert p.parts[-3:] == ("data.sec.gov", "submissions", "CIK0000027419.json")
    assert p.is_relative_to(edgar_fetch.RAW)


def test_query_string_is_part_of_cache_key():
    base = "https://efts.sec.gov/LATEST/search-index"
    a, b = (edgar_fetch.cache_path(f"{base}?q=%22NEW+HOLDINGS%22&forms={f}") for f in ("13F-HR/A", "13F-NT"))
    assert a != b and a == edgar_fetch.cache_path(f"{base}?q=%22NEW+HOLDINGS%22&forms=13F-HR/A")
    assert a.parent == edgar_fetch.RAW / "efts.sec.gov" / "LATEST" / "search-index.q"
    assert edgar_fetch.cache_path(base) == edgar_fetch.RAW / "efts.sec.gov" / "LATEST" / "search-index"


def test_download_refuses_without_user_agent(monkeypatch, tmp_path):
    monkeypatch.setattr(edgar_fetch, "RAW", tmp_path)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(RuntimeError, match="SEC_USER_AGENT"):
        edgar_fetch.get("https://www.sec.gov/not-cached")


class _Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def test_get_fetches_each_query_once(monkeypatch, tmp_path):
    monkeypatch.setattr(edgar_fetch, "RAW", tmp_path)
    monkeypatch.setattr(edgar_fetch, "RATE", 1000.0)
    monkeypatch.setenv("SEC_USER_AGENT", "Test test@example.com")
    calls = []
    monkeypatch.setattr(edgar_fetch.urllib.request, "urlopen",
                        lambda req, timeout: calls.append(req.full_url) or _Resp(req.full_url.encode()))
    a, b = "https://efts.sec.gov/LATEST/search-index?q=a", "https://efts.sec.gov/LATEST/search-index?q=b"
    assert [edgar_fetch.get(u) for u in (a, b, a)] == [a.encode(), b.encode(), a.encode()]
    assert calls == [a, b] and not list(tmp_path.rglob("*.part"))


def test_throttle_spaces_requests_across_processes(tmp_path):
    code = ("import sys; from pathlib import Path; from data_layer import edgar_fetch as e; "
            "e.RAW = Path(sys.argv[1]); e.RATE = 20.0; print(*[e.throttle() for _ in range(4)])")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], cwd=ROOT, stdout=subprocess.PIPE, text=True)
             for _ in range(3)]
    starts = sorted(float(x) for p in procs for x in p.communicate(timeout=60)[0].split())
    assert len(starts) == 12
    assert min(b - a for a, b in zip(starts, starts[1:])) >= 1 / 20.0 - 1e-6
