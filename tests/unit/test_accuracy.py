import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import accuracy_test as at  # noqa: E402

AUDIO = Path(__file__).resolve().parents[1] / "audio"


def test_normalize_folds_script_variants():
    # Arabic yeh/kaf, diacritics, Urdu full stop, Urdu digits.
    assert at.normalize("كيا؟") == at.normalize("کیا")
    assert at.normalize("مَنَنَه۔") == "مننه"
    assert at.normalize("۴ بجے") == "4 بجے"


def test_error_rate():
    assert at.error_rate("a b c".split(), "a b c".split()) == 0
    assert at.error_rate("a b c d".split(), "a x c".split()) == 0.5
    assert at.error_rate([], []) == 0


def test_key_terms_and_alternates():
    assert at.term_found("4/char", "maspakhin char bajay")
    assert not at.term_found("imran", "zama num irfan de")


def test_example_files_score():
    manifest = at.read_csv(AUDIO / "manifest.example.csv")
    hyps = {r["clip"]: r["hypothesis"] for r in at.read_csv(AUDIO / "hyps.example.csv")}
    results = {r.clip: r for r in at.score(manifest, hyps, max_wer=0.25)}
    assert results["ps_003.wav"].usable
    assert results["ur_001.wav"].usable          # trailing "?" ignored
    assert not results["ps_002.wav"].usable      # "saba" != "sabaa" key term
    assert results["mx_001.wav"].missing
    overall = at.summarize(list(results.values()))["overall"]
    assert overall["clips"] == 6 and overall["missing"] == 1
