"""Extract real SEC responses recorded in edgartools VCR cassettes (offline fixtures).

Usage: python tools/extract_cassettes.py [--write]
Scans vendor/edgartools/tests/cassettes/*.yaml, classifies every EDGAR full-submission
(.txt), companyfacts JSON and submissions JSON, prints a TSV index, and with --write saves
13F and DEF 14A submissions and companyfacts JSON under fixtures/sec/.
Cassettes are loaded with a safe YAML loader (unknown python/ tags are ignored).
"""
import base64, gzip, json, re, sys, zlib
from pathlib import Path
import yaml

HERE = Path(__file__).resolve().parent.parent
CASS = HERE / "vendor/edgartools/tests/cassettes"
OUT = HERE / "fixtures/sec"

class Loader(yaml.CSafeLoader if hasattr(yaml, "CSafeLoader") else yaml.SafeLoader):
    pass
Loader.add_multi_constructor("tag:yaml.org,2002:python/", lambda l, s, n: None)

def body_text(resp):
    b = resp.get("body", {}) or {}
    s = b.get("string", b) if isinstance(b, dict) else b
    if isinstance(s, str):
        return s
    if isinstance(s, (bytes, bytearray)):
        data = bytes(s)
        enc = " ".join((resp.get("headers") or {}).get("Content-Encoding", []) or []).lower()
        for fn in (gzip.decompress, lambda d: zlib.decompress(d, -15), lambda d: d):
            try:
                return fn(data).decode("utf-8", "replace")
            except Exception:
                continue
    return ""

def hdr(txt, key):
    m = re.search(rf"{key}:\s*([^\n]+)", txt)
    return m.group(1).strip() if m else ""

def main(write=False):
    seen = {}
    for f in sorted(CASS.glob("*.yaml")):
        try:
            d = yaml.load(f.read_text(errors="replace"), Loader=Loader)
        except Exception as e:
            print(f"# skip {f.name}: {e}", file=sys.stderr); continue
        for it in (d or {}).get("interactions", []) or []:
            uri = it["request"]["uri"]
            if uri in seen or not (uri.endswith(".txt") or "companyfacts" in uri):
                continue
            txt = body_text(it["response"])
            if uri.endswith(".txt") and "<SEC-HEADER>" in txt[:500]:
                form = hdr(txt, "CONFORMED SUBMISSION TYPE")
                rec = dict(kind="submission", form=form, period=hdr(txt, "CONFORMED PERIOD OF REPORT"),
                           filed=hdr(txt, "FILED AS OF DATE"), name=hdr(txt, "COMPANY CONFORMED NAME"),
                           cik=hdr(txt, "CENTRAL INDEX KEY"), acc=hdr(txt, "ACCESSION NUMBER"))
                if form.startswith("13F"):
                    rec["schema"] = (re.findall(r"<schemaVersion>([^<]+)", txt) or [""])[0]
                    rec["amend"] = (re.findall(r"<amendmentType>([^<]+)", txt) or [""])[0]
                    rec["n_rows"] = len(re.findall(r"<(?:\w+:)?infoTable>", txt))
                    rec["total"] = (re.findall(r"<tableValueTotal>([^<]+)", txt) or [""])[0]
                    rec["reportType"] = (re.findall(r"<reportType>([^<]+)", txt) or [""])[0]
            elif "companyfacts" in uri and txt.startswith("{"):
                j = json.loads(txt)
                rec = dict(kind="companyfacts", form="companyfacts", name=j.get("entityName", ""),
                           cik=str(j.get("cik", "")), acc="", period="", filed="",
                           n_concepts=sum(len(v) for v in j.get("facts", {}).values()))
            else:
                continue
            rec.update(cassette=f.name, uri=uri, bytes=len(txt))
            seen[uri] = rec
            keep = rec["form"].startswith(("13F", "DEF 14A")) or rec["kind"] == "companyfacts"
            if write and keep:
                sub = "companyfacts" if rec["kind"] == "companyfacts" else re.sub(r"[^A-Za-z0-9]+", "", rec["form"])
                name = uri.rsplit("/", 1)[-1]
                p = OUT / sub / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(txt)
                rec["saved"] = str(p.relative_to(HERE))
    cols = ["form", "name", "cik", "period", "filed", "acc", "schema", "amend", "reportType", "n_rows", "total", "n_concepts", "cassette", "saved"]
    print("\t".join(cols))
    for r in sorted(seen.values(), key=lambda r: (r["form"], r["filed"])):
        print("\t".join(str(r.get(c, "")) for c in cols))
    if write:
        (OUT / "index.json").write_text(json.dumps(list(seen.values()), indent=1))

if __name__ == "__main__":
    main("--write" in sys.argv)
