# genvol -- volume generator for COBOL analysis tooling

Generates thousands of realistic COBOL/CICS/Db2 programs, plus the copybooks,
JCL, BMS maps, CSD definitions and DDL that surround them, together with a
`manifest.json` holding the **exact expected call graph**.

The point is not just volume. Cloning `base/src` a thousand times gives you
bytes but a degenerate graph, so it tells you nothing about whether a tool's
graph output is *correct*. `genvol` builds the topology first -- layers,
domains, hubs, cycles, long chains, orphans, dynamic dispatch -- and then emits
source that materialises it, so you can score precision and recall instead of
eyeballing the result.

Nothing generated here is meant to run on z/OS. It is meant to be read by
tools: fixed-format is respected (nothing past column 72), but there is no
compile or bind step.

## Usage

```sh
python3 tools/genvol/genvol.py -n 5000 -o /var/tmp/volume
```

No dependencies beyond the Python 3 standard library and no network access.
Throughput is roughly linear: 5.000 programs (~2,5 M lines, ~108 MiB) in about a
second, 100.000 (~50 M lines, ~2,1 GiB) in about 18.

Program names are 8 characters — `Z` + 2-char domain + 5-char serial — matching
the z/OS member-name limit. The serial is decimal up to 99.999 programs and
switches to base 36 above that (60M names), so corpora that fit in decimal stay
byte-identical to earlier runs with the same seed.

Useful flags:

| Flag | Default | Purpose |
|---|---|---|
| `-n, --programs` | 2000 | program count (pathological cases are added on top) |
| `--avg-lines` | 600 | size centre; distribution is log-normal, so expect a long tail |
| `--domains` | 10 | business domains, which drive naming and clustering |
| `--hub-density` | 0.55 | share of programs that LINK to a shared utility |
| `--hubs` | 6 | number of high-fan-in utilities. Deliberately does **not** scale with `-n`: a proportional share would give 200 middling "hubs" at 100k programs instead of a few real ones |
| `--dynamic-rate` | 0.12 | share of edges rewritten as dynamic dispatch |
| `--cycles` | 12 | injected recursion rings (size 1--4) |
| `--chains` / `--chain-depth` | 6 / 18 | long linear call chains, for traversal depth |
| `--dead-code-rate` | 0.08 | share of paragraphs left unreferenced |
| `--pathological` | 10 | number of deliberately hostile programs, 0 disables |
| `--boundary` | 0 | programs sized to straddle a context window, at 0.5/0.8/0.95/1.02/1.15/1.5/2.0× the budget (pass 14 for two of each) |
| `--window-tokens` | 200000 | the target model's context window |
| `--chars-per-token` | 3.5 | **measure this** with `count_tokens`; the default is a placeholder |
| `--window-reserve` | 0.15 | share of the window held back for prompt and output |
| `--seqnums` | off | sequence numbers in cols 73--80, as in a real z/OS export |
| `--layout` | domain | `domain` nests under `src/<DD>/`, `flat` puts everything in `src/` |
| `--gzip-manifest` | off | write `manifest.json.gz` instead of `manifest.json`. Required above ~90k programs, where the plain manifest passes GitHub's 100 MiB per-file limit |
| `--seed` | 20260729 | output is fully deterministic for a given seed |

Scale is bounded by the free subroutine pool: `--chains 20 --chain-depth 18`
needs 360 subroutines, so roughly `-n 1700` or more. The generator prints a
note and reduces the count rather than failing silently.

## What gets generated

```
generated/
  src/<DOMAIN>/Z<DD>NNNNN.cbl    programs, grouped by business domain
  src/attic/                     one deliberately duplicated PROGRAM-ID
  copybook/<DOMAIN>/ZK*.cpy      record layouts, commarea, constants
  bms/Z<DD>MAPnn.bms             mapsets for the online drivers
  jcl/VJOBnnnn.jcl               multi-step jobs -> EXEC PGM= edges
  cntl/csdvol.txt                CSD input: TRANSID -> program bindings
  ddl/schema.sql                 tables referenced from EXEC SQL
  manifest.json                  ground truth
```

Programs follow the GenApp base layering: online drivers (BMS, `EXEC CICS
LINK`) over business logic over a data layer split between `EXEC SQL` and VSAM
file control, with standalone subroutines, batch programs and a small set of
high-fan-in utilities modelled on `LGSTSQ`.

## Edge kinds

The generator deliberately spans the full difficulty range, because a tool that
only reports literal `LINK` edges will look perfect on easy input:

| Kind | Resolvable statically? |
|---|---|
| `CICS_LINK`, `CICS_XCTL` on a literal | yes |
| `CALL_STATIC` on a literal | yes |
| `JCL_EXEC_PGM` | yes |
| `CICS_START` via TRANSID | only by cross-referencing `cntl/csdvol.txt` |
| `CICS_LINK_DYNAMIC`, `CALL_DYNAMIC` | needs dataflow: the name arrives in a working-storage field, sometimes from an `OCCURS` dispatch table |

`manifest.json` records the true target for every edge including the dynamic
ones, plus the source line of the verb, so a tool can be scored on location as
well as existence.

## Context-window boundary

An ordinary size distribution barely exercises a 200K-token window — almost
every program fits, so a tool's chunking path never runs. `--boundary N`
generates programs at known multiples of the window budget and records each one
in `injected.boundary` with its `multiple_of_window` and a `fits_window` flag,
so the tool can be scored on *where* it starts chunking and whether it stays
correct across the line.

```sh
python3 tools/genvol/genvol.py -n 5000 --boundary 14 \
  --window-tokens 200000 --chars-per-token 3.4 -o /var/tmp/volume
```

The cohort is window-specific. Generate against the **smaller** of your target
windows — the 1.5× and 2.0× entries then also bracket a window up to twice that
size. Tokenizers differ between providers, so measure `--chars-per-token` per
model rather than reusing one figure.

### Measuring the ratio — not optional

`measure_ratio.py` measures it against a corpus you already generated:

```sh
python3 tools/genvol/measure_ratio.py /path/to/corpus --model claude-haiku-4-5
python3 tools/genvol/measure_ratio.py /path/to/corpus --dry-run   # no API calls

# through an existing AWS session instead of an API key
python3 tools/genvol/measure_ratio.py /path/to/corpus --provider bedrock \
    --aws-region eu-north-1 --model claude-haiku-4-5
```

`--provider bedrock` needs `pip install "anthropic[bedrock]" "botocore[crt]"` and
an account that has been **granted access to the model** — a valid AWS session is
not enough on its own, and a missing grant surfaces as
`403 … is not available for this account`. Bedrock's Messages-API endpoint also
wants the bare prefixed ID (`anthropic.claude-haiku-4-5`); the dated form that
`aws bedrock list-foundation-models` prints returns 404 there. The script adds
the prefix for you.

It counts the largest programs exactly — that is where overflow risk lives —
plus a spread across the size range, and counts each program **together with its
copybook closure**, since that is what a tool actually hands the model.

The statistic that matters is the **minimum** ratio, not the mean: a lower ratio
means more tokens per character, so the budget has to come from the worst case.
An average that looks comfortable still hides the files that overflow.

Judge the cap by the ratio at the **big end**, not the global minimum. Small
files score systematically lower — the fixed message-envelope overhead is a large
share of a short payload — and they are nowhere near the window, so a global
minimum condemns a perfectly safe corpus. The script reports both and derives its
verdict from the largest files.

Measured for `anthropic.claude-haiku-4-5-20251001-v1:0` on Bedrock against both
published corpora: **3,63–3,66 chars/token** at the big end, versus 3,26–3,28
overall. A 12.000-line program (~550 KB with its copybooks) counts about
**151.000 tokens**, leaving 11% headroom inside a 200.000-token window with a 15%
reserve. The `--chars-per-token 3.4` those corpora were generated with turned out
conservative; measure yours rather than reusing this figure, since it is
tokenizer-specific.

### Keeping the whole corpus inside one window

Until there is a fallback path for over-window files, `--fit-window` caps every
program below the budget: the giant is shrunk to the largest file that still
fits (rather than dropped, so a biggest-file stress case remains), and the
boundary cohort keeps only its ≤ 1.0× multiples. `manifest.json` records the cap
under `window_cap` so a run can't be mistaken for an uncapped one.

```sh
python3 tools/genvol/genvol.py -n 5000 --boundary 12 \
  --fit-window --window-tokens 200000 --chars-per-token 3.4 -o /var/tmp/volume
```

Drop the flag once the fallback exists and the over-window cases come back —
the giant returns to ~20.000 lines and the 1.02×/1.15×/1.5×/2.0× cohort
reappears, which is what exercises the routing decision itself.

## Hostile cases

One program each, tagged in the manifest: 120-level `IF` nesting; `GO TO
DEPENDING ON` spaghetti; obsolete `ALTER ... TO PROCEED TO`; 300 outbound
`CALL`s; dispatch resolved from a table of names; a single ~20.000-line
program; lower case source with embedded tabs; `EXEC SQL PREPARE` from a host
variable; a large program with no callers; and the same `PROGRAM-ID` present in
two directories.

## Scoring a tool against the manifest

```python
import json
truth = json.load(open("generated/manifest.json"))
expected = {
    (p["name"], c["target"])
    for p in truth["programs"] for c in p["calls"]
}
# actual = {(caller, callee), ...} from the tool under test
print("recall   ", len(actual & expected) / len(expected))
print("precision", len(actual & expected) / len(actual))
```

`expected_graph` in the manifest holds the aggregate figures worth checking
first: node and edge counts, edges by kind, how many need dataflow or the CSD,
the injected cycles, entry points, orphans, maximum call depth, and the
copybook fan-in ranking.

Two figures deserve care when comparing. `max_call_depth_sampled` is computed
from at most 400 roots, so it is a lower bound on very large estates. Orphans
exclude online drivers with a TRANSID and JCL jobs, which are entry points by
design rather than dead code.
