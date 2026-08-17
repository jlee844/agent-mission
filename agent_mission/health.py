"""Session health: facts about HOW the session ran, not whether it was right.

Deliberately not drift detection. Every signal here is a mechanical property of
the transcript or the filesystem, with no judgement about intent:

  model changes   the model was swapped mid-session. Behaviour changing after
                  an update is not imagination -- it was a different model.
  repeats         a response that closely matches an earlier one. At cosine
                  1.000 it is byte-identical: the agent answered a turn it had
                  already answered.
  collisions      two live sessions editing the same file, which is a live
                  hazard whenever more than one agent runs in a tree.

No dependencies: the similarity is TF-IDF cosine in pure Python. It is coarse,
and that is fine — the interesting cases sit far above the noise.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# Numbers are tokens. Without them, two replies differing only in figures --
# "40/62" versus "41/62" -- score 1.000 and read as a verbatim repeat, which is
# exactly the false positive this signal cannot afford.
TOK = re.compile(r"[a-z]{3,}|\d+")
MIN_CHARS = 200          # short replies repeat innocently ("Done.")
NEAR = 0.55              # measured: median ~0.22, p95 ~0.40 across four sessions
EXACT = 0.999


@dataclass
class Health:
    models: dict[str, int] = field(default_factory=dict)
    model_order: list[str] = field(default_factory=list)
    repeats: list[dict] = field(default_factory=list)
    n_responses: int = 0

    @property
    def model_changed(self) -> bool:
        return len(self.model_order) > 1

    @property
    def exact_repeats(self) -> int:
        return sum(1 for r in self.repeats if r["sim"] >= EXACT)


def _vectors(docs: list[list[str]]) -> list[dict[str, float]]:
    """Sparse TF-IDF, L2-normalised. Sparse because most pairs share little."""
    df: Counter[str] = Counter()
    for d in docs:
        df.update(set(d))
    n = len(docs)
    out = []
    for d in docs:
        tf = Counter(d)
        v = {w: c * math.log(n / (1 + df[w])) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({w: x / norm for w, x in v.items()})
    return out


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def inspect(path: Path, cap: int = 400) -> Health:
    h = Health()
    texts: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return h
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message") or {}
        model = msg.get("model")
        if model and not str(model).startswith("<"):
            h.models[model] = h.models.get(model, 0) + 1
            if not h.model_order or h.model_order[-1] != model:
                h.model_order.append(model)
        if msg.get("role") != "assistant":
            continue
        c = msg.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                t = " ".join((b.get("text") or "").split())
                if len(t) >= MIN_CHARS and len(texts) < cap:
                    texts.append(t)

    h.n_responses = len(texts)
    if len(texts) < 20:
        return h

    vecs = _vectors([TOK.findall(t.lower()) for t in texts])
    for i in range(1, len(vecs)):
        best, bj = 0.0, -1
        for j in range(i):
            s = _cos(vecs[i], vecs[j])
            if s > best:
                best, bj = s, j
        if best >= NEAR:
            h.repeats.append({
                "sim": round(best, 3), "i": i, "j": bj, "gap": i - bj,
                "text": texts[i][:150],
                "exact": best >= EXACT,
            })
    h.repeats.sort(key=lambda r: -r["sim"])
    return h


def collisions(sessions: dict[str, dict[str, int]]) -> list[dict]:
    """Files more than one live session has written.

    Two agents editing one file is a live hazard the moment you run more than
    one in a tree, and nothing in either session can see the other.
    """
    who: dict[str, list[str]] = {}
    for sid, files in sessions.items():
        for f in files:
            who.setdefault(f, []).append(sid)
    return sorted(
        ({"file": f, "sessions": s} for f, s in who.items() if len(s) > 1),
        key=lambda d: -len(d["sessions"]))
