"""Turn a plain wake phrase into the BPE tokens the KWS model expects.

The model does not need retraining to learn a new phrase — it decodes against
a token list, so changing the wake word is a text transformation.

    python client/tool/make_keywords.py "HEY PAI"

Writes the result to client/assets/kws/keywords.txt. Needs `sentencepiece`
(pip install sentencepiece); it is a build-time tool, not a runtime dependency,
so it is deliberately absent from requirements.txt.

Keep phrases upper-case and at least three or four tokens long — short ones
false-trigger badly. Add ':score' or '#threshold' after a phrase to tune it
individually; see docs/wake-word.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import sentencepiece as spm

HERE = Path(__file__).resolve().parent
BPE = HERE / "kws-bpe.model"
OUT = HERE.parent / "assets" / "kws" / "keywords.txt"


def main(phrases: list[str]) -> int:
    if not phrases:
        print(__doc__)
        return 1
    if not BPE.exists():
        print(f"missing {BPE}", file=sys.stderr)
        return 1

    sp = spm.SentencePieceProcessor()
    sp.load(str(BPE))

    lines = []
    for phrase in phrases:
        tokens = sp.encode(phrase.upper(), out_type=str)
        if len(tokens) < 3:
            print(f"warning: {phrase!r} is only {len(tokens)} tokens; "
                  "expect false triggers", file=sys.stderr)
        lines.append(" ".join(tokens))
        print(f"{phrase.upper()} -> {' '.join(tokens)}")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
