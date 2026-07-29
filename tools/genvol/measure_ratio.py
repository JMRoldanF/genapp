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

    # via Amazon Bedrock, using an existing AWS session
    python3 measure_ratio.py /path/to/corpus --provider bedrock \
        --aws-region eu-north-1 --model claude-haiku-4-5

Needs the `anthropic` SDK and credentials: ANTHROPIC_API_KEY or an
`ant auth login` profile for `--provider api`; an AWS session for
`--provider bedrock` (which additionally requires that the account has been
granted access to the model) or `--provider aws`. Use --dry-run to inspect the
sample selection without calling anything.
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


def payload_for(corpus: str, prog: dict,
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
    ap.add_argument("--provider",
                    choices=("api", "bedrock", "bedrock-native", "aws"),
                    default="api",
                    help="api: first-party Claude API. bedrock: Bedrock's "
                         "Messages-API (Mantle) endpoint, short prefixed model "
                         "IDs. bedrock-native: Bedrock's own CountTokens "
                         "operation via boto3, which accepts the dated "
                         "InvokeModel IDs. aws: Claude Platform on AWS")
    ap.add_argument("--aws-region", default=os.environ.get("AWS_REGION"),
                    help="required for --provider bedrock/aws")
    ap.add_argument("--aws-workspace-id",
                    default=os.environ.get("ANTHROPIC_AWS_WORKSPACE_ID"),
                    help="required for --provider aws")
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
            n = len(payload_for(cfg.corpus, p, closure, cb_path))
            print(f"{p['name']:<10} {p['lines']:>8,} {n:>14,}")
        print("\n--dry-run: no API calls made")
        return 0

    counter, model = make_counter(cfg)
    rows = []
    for i, p in enumerate(sample, 1):
        text = payload_for(cfg.corpus, p, closure, cb_path)
        try:
            tokens = counter(text)
        except Exception as exc:                      # noqa: BLE001
            sys.exit(f"token count failed on {p['name']} ({model}): {exc}")
        rows.append((p["name"], p["lines"], len(text), tokens, len(text) / tokens))
        print(f"\r  {i}/{len(sample)}", end="", file=sys.stderr)
    print("\r" + " " * 20 + "\r", end="", file=sys.stderr)
    return report(rows, window, reserve, assumed)


def make_counter(cfg):
    """Return (count_fn, resolved_model_id) for the selected provider."""
    if cfg.provider == "bedrock-native":
        # Bedrock's own CountTokens operation. Unlike the Messages-API endpoint
        # it accepts the dated InvokeModel IDs (…-20251001-v1:0), and unlike the
        # legacy anthropic AnthropicBedrock client it actually supports counting
        # -- that one raises "Token counting is not supported in Bedrock yet".
        try:
            import boto3
        except ImportError:
            sys.exit("pip install boto3")
        if not cfg.aws_region:
            sys.exit("--provider bedrock-native needs --aws-region")
        rt = boto3.client("bedrock-runtime", region_name=cfg.aws_region)
        model = cfg.model

        def count(text: str) -> int:
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1,
                "messages": [{"role": "user", "content": text}],
            })
            resp = rt.count_tokens(
                modelId=model, input={"invokeModel": {"body": body}})
            return resp["inputTokens"]

        return count, model

    try:
        import anthropic
    except ImportError:
        extra = {"bedrock": "[bedrock]", "aws": "[aws]"}.get(cfg.provider, "")
        sys.exit(f'pip install "anthropic{extra}"')

    model = cfg.model
    if cfg.provider == "bedrock":
        if not cfg.aws_region:
            sys.exit("--provider bedrock needs --aws-region (or AWS_REGION)")
        # Bedrock model IDs carry an 'anthropic.' prefix. Note the Messages-API
        # endpoint wants the bare prefixed form: dated/versioned IDs such as
        # anthropic.claude-haiku-4-5-20251001-v1:0 return 404 there, even though
        # `aws bedrock list-foundation-models` lists them.
        if not model.startswith("anthropic."):
            model = "anthropic." + model
        client = anthropic.AnthropicBedrockMantle(aws_region=cfg.aws_region)
    elif cfg.provider == "aws":
        if not cfg.aws_region:
            sys.exit("--provider aws needs --aws-region (or AWS_REGION)")
        if not cfg.aws_workspace_id:
            sys.exit("--provider aws needs --aws-workspace-id "
                     "(or ANTHROPIC_AWS_WORKSPACE_ID)")
        client = anthropic.AnthropicAWS(aws_region=cfg.aws_region,
                                        workspace_id=cfg.aws_workspace_id)
    else:
        client = anthropic.Anthropic()

    def count(text: str) -> int:
        return client.messages.count_tokens(
            model=model, messages=[{"role": "user", "content": text}],
        ).input_tokens

    return count, model


def report(rows, window: int, reserve: float, assumed: float) -> int:
    usable = window * (1.0 - reserve)
    ratios = [r[4] for r in rows]

    print(f"{'program':<10} {'lines':>8} {'chars':>10} {'tokens':>9} {'ch/tok':>7}")
    for name, lines, chars, tokens, ratio in rows[-12:]:
        print(f"{name:<10} {lines:>8,} {chars:>10,} {tokens:>9,} {ratio:>7.2f}")

    # Small files have a systematically lower ratio: the fixed message-envelope
    # overhead is a large share of a short payload. Those files are nowhere near
    # the window, so judging the cap by the global minimum would condemn a
    # perfectly safe corpus. The verdict has to come from the big end.
    big = rows[-max(3, len(rows) // 4):]
    r_min_big = min(r[4] for r in big)
    print(f"\nratio overall  min {min(ratios):.2f}  mean"
          f" {sum(ratios) / len(ratios):.2f}  max {max(ratios):.2f}")
    print(f"ratio big end  min {r_min_big:.2f}   <- the one that governs the cap")
    print("  (small files score lower: the fixed envelope overhead dominates a"
          " short payload)")

    worst_name, _, worst_chars, worst_tokens, _ = max(rows, key=lambda r: r[3])
    headroom = usable - worst_tokens
    print(f"\nlargest measured  {worst_name}  {worst_chars:,} chars ->"
          f" {worst_tokens:,} tokens")
    print(f"usable budget     {int(usable):,} tokens"
          f" ({window:,} less {reserve:.0%} reserve)")
    print(f"headroom          {int(headroom):,} tokens"
          f" ({headroom / usable:+.0%})")

    over = [r for r in rows if r[3] > usable]
    if over:
        print(f"\nFAIL: {len(over)} measured programs exceed the usable budget:")
        for name, lines, _, tokens, _ in over:
            print(f"  {name}  {tokens:,} tokens  ({lines:,} lines)")
        print(f"Regenerate with --chars-per-token {r_min_big:.2f}.")
        return 1

    if r_min_big < assumed:
        print(f"\nWARNING: the big-end ratio {r_min_big:.2f} is below the assumed"
              f" {assumed}.")
        print("Nothing measured overflows, but the margin is thinner than"
              " intended -- unmeasured files near the cap could exceed it.")
        print(f"Regenerate with --chars-per-token {r_min_big:.2f} to restore it.")
        return 0

    print(f"\nOK: the assumed {assumed} was conservative (real big-end ratio"
          f" {r_min_big:.2f}).")
    print("Every measured program fits, with margin. No regeneration needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
