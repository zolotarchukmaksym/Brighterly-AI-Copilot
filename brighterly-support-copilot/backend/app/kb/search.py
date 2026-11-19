"""Tiny BM25 over the FAQ markdown files."""
from __future__ import annotations
import math, re
from pathlib import Path
from functools import lru_cache

_STOP = set("a an the is are was were i my me we our to of in on for and or it this that with can do you your be have has not no at as by from if so".split())


def _tok(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in _STOP]


@lru_cache(maxsize=1)
def _docs() -> list[dict]:
    out = []
    for p in sorted((Path(__file__).parent / "faq").glob("*.md")):
        raw = p.read_text(encoding="utf-8")
        _, fm, body = raw.split("---", 2)
        meta = dict(l.split(": ", 1) for l in fm.strip().splitlines())
        text = meta["title"] + " " + body.strip()
        out.append({"id": meta["id"], "title": meta["title"], "text": body.strip(), "tokens": _tok(text)})
    return out


def search(query: str, k: int = 3) -> list[dict]:
    docs = _docs()
    q = _tok(query)
    N = len(docs)
    avg = sum(len(d["tokens"]) for d in docs) / N
    df = {t: sum(1 for d in docs if t in d["tokens"]) for t in set(q)}
    res = []
    for d in docs:
        s = 0.0
        for t in q:
            f = d["tokens"].count(t)
            if not f:
                continue
            idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * len(d["tokens"]) / avg))
        if s > 0:
            res.append((s, d))
    res.sort(key=lambda x: -x[0])
    return [{"id": d["id"], "title": d["title"], "snippet": d["text"][:600], "score": round(s, 2)} for s, d in res[:k]]
