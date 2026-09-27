#!/usr/bin/env python3
"""Week-0 STT accuracy test for Pashto / Urdu / mixed call audio.

Compares speech-to-text output against human reference transcripts and
reports, per clip and per bucket (language, noise):

  - WER / CER            how far the transcript is from the reference
  - key-term recall      did the words that matter for a booking survive
                         (names, days, times, numbers, service names)
  - usable               WER <= --max-wer AND every key term found

"Usable" is the number that matters: a clip where the agent would have
booked the right person at the right time.

Inputs (see tests/audio/README.md):
  manifest   CSV: clip,language,dialect,noise,reference,key_terms
  hyps       CSV: clip,hypothesis   (one file per STT provider)

Usage:
  python scripts/accuracy_test.py tests/audio/manifest.csv results/deepgram.csv
  python scripts/accuracy_test.py manifest.csv a.csv b.csv --max-wer 0.3 --json out.json

Stdlib only, so it runs anywhere.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

# --- Normalization ----------------------------------------------------------

# Arabic-script variants that STT engines and human transcribers mix freely.
# Only fold letters that never distinguish words in Urdu or Pashto.
_CHAR_MAP = str.maketrans({
    "ي": "ی",  # Arabic yeh ي -> Farsi/Urdu yeh ی
    "ى": "ی",  # alef maksura ى -> ی
    "ك": "ک",  # Arabic kaf ك -> keheh ک
    "ہ": "ه",  # Urdu heh goal ہ -> heh ه (Pashto uses ه)
    "ە": "ه",  # ae ە -> ه
    "ة": "ه",  # teh marbuta ة -> ه
    "أ": "ا",  # أ -> ا
    "إ": "ا",  # إ -> ا
    "ٱ": "ا",  # ٱ -> ا
})

# Harakat, tanween, superscript alef, tatweel: optional in real writing.
_DIACRITICS = re.compile("[ً-ٰٟـ]")
# Punctuation incl. Urdu full stop ۔, Arabic comma ، and question mark ؟.
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
# Eastern Arabic / Urdu digits -> ASCII so "۴" == "4".
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = text.translate(_CHAR_MAP).translate(_DIGITS)
    text = _DIACRITICS.sub("", text)
    text = _PUNCT.sub(" ", text.lower())
    return " ".join(text.split())


# --- Metrics ----------------------------------------------------------------

def edit_distance(ref: list, hyp: list) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1]


def error_rate(ref: list, hyp: list) -> float:
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)


def parse_key_terms(raw: str) -> list[str]:
    # Pipe-separated; each term may list alternates with "/", e.g. "4/char|imran".
    return [t.strip() for t in (raw or "").split("|") if t.strip()]


def term_found(term: str, hyp_norm: str) -> bool:
    padded = f" {hyp_norm} "
    return any(f" {normalize(alt)} " in padded for alt in term.split("/") if normalize(alt))


# --- Scoring ----------------------------------------------------------------

@dataclass
class ClipResult:
    clip: str
    language: str
    dialect: str
    noise: str
    wer: float
    cer: float
    key_terms: int
    key_terms_found: int
    usable: bool
    missing: bool
    reference: str
    hypothesis: str


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def score(manifest: list[dict], hyps: dict[str, str], max_wer: float) -> list[ClipResult]:
    results = []
    for row in manifest:
        clip = row["clip"].strip()
        ref_n = normalize(row.get("reference", ""))
        missing = clip not in hyps
        hyp_n = normalize(hyps.get(clip, ""))
        terms = parse_key_terms(row.get("key_terms", ""))
        found = sum(term_found(t, hyp_n) for t in terms)
        wer = error_rate(ref_n.split(), hyp_n.split())
        cer = error_rate(list(ref_n.replace(" ", "")), list(hyp_n.replace(" ", "")))
        results.append(ClipResult(
            clip=clip,
            language=(row.get("language") or "?").strip(),
            dialect=(row.get("dialect") or "").strip(),
            noise=(row.get("noise") or "?").strip(),
            wer=round(wer, 4),
            cer=round(cer, 4),
            key_terms=len(terms),
            key_terms_found=found,
            usable=not missing and wer <= max_wer and found == len(terms),
            missing=missing,
            reference=ref_n,
            hypothesis=hyp_n,
        ))
    return results


def summarize(results: list[ClipResult]) -> dict:
    def agg(rs: list[ClipResult]) -> dict:
        n = len(rs)
        terms = sum(r.key_terms for r in rs)
        return {
            "clips": n,
            "usable": sum(r.usable for r in rs),
            "usable_pct": round(100 * sum(r.usable for r in rs) / n, 1) if n else 0.0,
            "mean_wer": round(sum(r.wer for r in rs) / n, 3) if n else 0.0,
            "mean_cer": round(sum(r.cer for r in rs) / n, 3) if n else 0.0,
            "key_term_recall": round(sum(r.key_terms_found for r in rs) / terms, 3) if terms else None,
            "missing": sum(r.missing for r in rs),
        }

    buckets: dict[str, list[ClipResult]] = defaultdict(list)
    for r in results:
        buckets[f"language={r.language}"].append(r)
        buckets[f"noise={r.noise}"].append(r)
    return {"overall": agg(results), **{k: agg(v) for k, v in sorted(buckets.items())}}


# --- Output -----------------------------------------------------------------

def print_report(name: str, results: list[ClipResult], summary: dict, verbose: bool) -> None:
    print(f"\n=== {name} ===")
    print(f"{'bucket':<22}{'clips':>6}{'usable':>8}{'%':>7}{'WER':>7}{'CER':>7}{'terms':>7}")
    for bucket, s in summary.items():
        recall = "-" if s["key_term_recall"] is None else f"{s['key_term_recall']:.2f}"
        print(f"{bucket:<22}{s['clips']:>6}{s['usable']:>8}{s['usable_pct']:>7}"
              f"{s['mean_wer']:>7.2f}{s['mean_cer']:>7.2f}{recall:>7}")
    if summary["overall"]["missing"]:
        print(f"! {summary['overall']['missing']} clip(s) have no hypothesis")

    failures = [r for r in results if not r.usable]
    if failures and verbose:
        print("\nNot usable:")
        for r in failures:
            why = "missing" if r.missing else f"WER {r.wer:.2f}, terms {r.key_terms_found}/{r.key_terms}"
            print(f"  {r.clip} [{r.language}/{r.noise}] {why}")
            print(f"    ref: {r.reference}")
            print(f"    hyp: {r.hypothesis}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("hyps", type=Path, nargs="+", help="one CSV per STT provider")
    ap.add_argument("--max-wer", type=float, default=0.25, help="usable threshold (default 0.25)")
    ap.add_argument("--json", type=Path, help="write full results to this file")
    ap.add_argument("-q", "--quiet", action="store_true", help="hide per-clip failures")
    args = ap.parse_args(argv)

    manifest = read_csv(args.manifest)
    if not manifest:
        print(f"{args.manifest}: no clips", file=sys.stderr)
        return 1

    out = {}
    for hyp_path in args.hyps:
        hyps = {r["clip"].strip(): r.get("hypothesis", "") for r in read_csv(hyp_path)}
        results = score(manifest, hyps, args.max_wer)
        summary = summarize(results)
        print_report(hyp_path.stem, results, summary, verbose=not args.quiet)
        out[hyp_path.stem] = {"summary": summary, "clips": [asdict(r) for r in results]}

    if args.json:
        args.json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
