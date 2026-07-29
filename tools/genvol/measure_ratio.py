#!/usr/bin/env python3
"""Measure the real characters-per-token ratio of a generated corpus.

`genvol --fit-window` sizes programs from a *assumed* chars-per-token ratio. If
the real ratio is lower than assumed, files you believe fit the context window
will overflow in production. This script measures it with the token-counting API
and reports whether the corpus is actually safe.

The statistic that matters is the **minimum** ratio observed, not the mean: a low
ratio means more tokens per character, so the window budget must be derived from
the worst case. Averages hide exactly the files that break.

What the model sees is the program plus the copybooks it COPYs, so that is what
gets measured -- not the .cbl alone.

    python3 measure_ratio.py /path/to/corpus --model claude-haiku-4-5

Needs credentials (ANTHROPIC_API_KEY, or an `ant auth login` profile) and the
`anthropic` SDK. Use --dry-run to inspect the sample selection without calling
the API.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys


def load_manifest(corpus: str) -> dict:
    gz = os.path.join(corpus, "manifest.json.gz")
    plain = os.path.join(corpus, "manifest.json")
    if os.path.exists(gz):
        with gzip.open(gz, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    if os.path.exists(plain):
        with open(plain, encoding="utf-8") as fh:
            return json.load(fh)
    sys.exit(f"no manifest.json or manifest.json.gz in {corpus}")


def copybook_closure(manifest: dict) -> dict[str, list[str]]:
    """Resolve each copybook's transitive COPY closure, including itself."""
    copies = {c["name"]: c.get("copies", []) for c in manifest["copybooks"]}
    resolved: dict[str, list[str]] = {}

    def walk(name: str, seen: set[str]) -> list[str]:
        if name in seen:
            return []
        seen.add(name)
        out = [name]
        for dep in copies.get(name, []):
            out.extend(walk(dep, seen))
        return out

    for name in copies:
        resolved[name] = walk(name, set())
    return resolved


def payload_for(corpus: str, prog: dict, manifest: dict,
                closure: dict[str, list[str]],
                cb_path: dict[str, str]) -> str:
    """The text a tool would actually hand the model: program + its copybooks."""
    parts = []
    seen: set[str] = set()
    for cb in prog.get("copybooks", []):
        for name in closure.get(cb, [cb]):
            if name in seen or name not in cb_path:
                continue
            seen.add(name)
            with open(os.path.join(corpus, cb_path[name]), encoding="utf-8") as fh:
                parts.append(fh.read())
    with open(os.path.join(corpus, prog["file"]), encoding="utf-8") as fh:
        parts.append(fh.read())
    return "\n".join(parts)


def pick_sample(progs: list[dict], top: int, strata: int) -> list[dict]:
    """The largest files (where overflow risk lives) plus a spread of the rest."""
    by_size = sorted(progs, key=lambda p: p["lines"])
    chosen = {id(p): p for p in by_size[-top:]}
    if strata and len(by_size) > strata:
        step = len(by_size) / strata
        for i in range(strata):
            p = by_size[int(i * step)]
            chosen.setdefault(id(p), p)
    return sorted(chosen.values(), key=lambda p: p["lines"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus", help="directory holding the generated corpus")
    ap.add_argument("--model", default="claude-haiku-4-5",
                    help="model to count against; counts are model-specific")
    ap.add_argument("--top", type=int, default=30,
                    help="largest programs to measure exactly (overflow risk)")
    ap.add_argument("--strata", type=int, default=50,
                    help="additional programs sampled across the size range")
    ap.add_argument("--window-tokens", type=int, default=0,
                    help="window to check against; default reads it from the "
                         "manifest's window_cap")
    ap.add_argument("--reserve", type=float, default=-1.0,
                    help="share held back for prompt and output; default reads "
                         "it from the manifest")
    ap.add_argument("--dry-run", action="store_true",
                    help="show the sample selection without calling the API")
    cfg = ap.parse_args(argv)

    manifest = load_manifest(cfg.corpus)
    cap = manifest.get("window_cap", {})
    window = cfg.window_tokens or cap.get("window_tokens") or 200_000
    reserve = cfg.reserve if cfg.reserve >= 0 else manifest["config"].get(
        "window_reserve", 0.15)
    assumed = cap.get("chars_per_token") or manifest["config"].get(
        "chars_per_token", 3.5)

    closure = copybook_closure(manifest)
    cb_path = {c["name"]: c["file"] for c in manifest["copybooks"]}
    sample = pick_sample(manifest["programs"], cfg.top, cfg.strata)

    print(f"corpus         {os.path.abspath(cfg.corpus)}")
    print(f"programs       {len(manifest['programs']):,}")
    print(f"model          {cfg.model}")
    print(f"window         {window:,} tokens, {reserve:.0%} reserved")
    print(f"assumed ratio  {assumed} chars/token"
          f"{' (from manifest)' if cap else ''}")
    print(f"measuring      {len(sample)} programs"
          f" ({cfg.top} largest + {cfg.strata} across the range)\n")

    if cfg.dry_run:
        print(f"{'program':<10} {'lines':>8} {'payload chars':>14}")
        for p in sample:
            n = len(payload_for(cfg.corpus, p, manifest, closure, cb_path))
            print(f"{p['name']:<10} {p['lines']:>8,} {n:>14,}")
        print("\n--dry-run: no API calls made")
        return 0

    try:
        import anthropic
    except ImportError:
        sys.exit("pip install anthropic")

    client = anthropic.Anthropic()
    rows = []
    for i, p in enumerate(sample, 1):
        text = payload_for(cfg.corpus, p, manifest, closure, cb_path)
        try:
            tokens = client.messages.count_tokens(
                model=cfg.model,
                messages=[{"role": "user", "content": text}],
            ).input_tokens
        except Exception as exc:                      # noqa: BLE001
            sys.exit(f"count_tokens failed on {p['name']}: {exc}")
        rows.append((p["name"], p["lines"], len(text), tokens, len(text) / tokens))
        print(f"\r  {i}/{len(sample)}", end="", file=sys.stderr)
    print("\r" + " " * 20 + "\r", end="", file=sys.stderr)

    ratios = [r[4] for r in rows]
    r_min, r_max = min(ratios), max(ratios)
    r_mean = sum(ratios) / len(ratios)

    print(f"{'program':<10} {'lines':>8} {'chars':>10} {'tokens':>9} {'ch/tok':>7}")
    for name, lines, chars, tokens, ratio in rows[-12:]:
        print(f"{name:<10} {lines:>8,} {chars:>10,} {tokens:>9,} {ratio:>7.2f}")
    print(f"\nratio: min {r_min:.2f}  mean {r_mean:.2f}  max {r_max:.2f}")

    # The safe budget uses the worst ratio seen, not the average.
    safe_budget = int(window * r_min * (1.0 - reserve))
    assumed_budget = int(window * assumed * (1.0 - reserve))
    print(f"budget at min ratio    {safe_budget:,} chars")
    print(f"budget at assumed {assumed}  {assumed_budget:,} chars")

    exact_over = [r for r in rows if r[3] > window * (1.0 - reserve)]
    print(f"\nmeasured programs over the window: {len(exact_over)}")
    for name, lines, chars, tokens, _ in exact_over:
        print(f"  {name}  {tokens:,} tokens  ({lines:,} lines)")

    if r_min < assumed:
        shortfall = (assumed - r_min) / assumed
        print(f"\nWARNING: the real minimum ratio is {shortfall:.0%} below the"
              f" assumed {assumed}.")
        print("The corpus was sized against the assumed value, so files near the"
              " cap may overflow.")
        print(f"Regenerate with --chars-per-token {r_min:.2f} to be safe.")
    else:
        print(f"\nOK: the assumed {assumed} is conservative"
              f" (real minimum {r_min:.2f}); the cap holds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
