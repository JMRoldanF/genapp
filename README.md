# genvol — volume generator for COBOL analysis tooling

This repository holds **[`tools/genvol/`](tools/genvol/README.md)**, a generator
that produces large synthetic COBOL/CICS/Db2 estates for stress-testing COBOL
analysis, explanation and call-graph tooling — together with a manifest holding
the exact expected call graph, so tool output can be scored for precision and
recall instead of eyeballed.

```sh
python3 tools/genvol/genvol.py -n 5000 -o /var/tmp/volume
```

Python 3 standard library only, no network access, deterministic per seed.
Roughly 5.000 programs per second. See
[`tools/genvol/README.md`](tools/genvol/README.md) for the options, the edge
kinds it spans, and how to score a tool against the manifest.

## Generated corpora

| Repository | Programs | Lines |
|---|---:|---:|
| [volume-10k](https://github.com/JMRoldanF/volume-10k) | 10.022 | 5,1 M |
| [volume-100k](https://github.com/JMRoldanF/volume-100k) | 100.034 | 50,1 M |

Both are capped so every program fits a 200.000-token context window
(`--fit-window`); regenerate without that flag once an over-window fallback
exists.

## Reference application: IBM GenApp

`genvol`'s output is modelled on the **IBM GenApp** sample — fixed-format COBOL,
eyecatchers in `WORKING-STORAGE`, commarea copybooks, `EXEC CICS LINK` to a
shared error hub, `EXEC SQL` in the data layer, VSAM in the file layer, and the
driver → business → data layering.

The generator does **not** read that code — it has no runtime dependency on it
and every template is authored fresh. GenApp is the fidelity reference: consult
it when calibrating realism, or as a genuine human-written control sample to
check that a tool scoring well on synthetic input also scores well on real code.

It lives in IBM's public repository rather than being vendored here:

**https://github.com/cicsdev/cics-genapp**

Wire it up as a git remote and the code is one fetch away (remotes are local
config, so each clone needs this once):

```sh
git remote add upstream https://github.com/cicsdev/cics-genapp.git
git fetch upstream
git show upstream/main:base/src/lgipol01.cbl     # read a program
git checkout upstream/main -- base/              # or check the tree out locally
```

The templates were calibrated against upstream commit **`f6f3f4b`** ("Minor
updates"). That tree was previously vendored here under `base/` and was
byte-identical to upstream; it was removed in favour of this pointer. To recover
it in full:

```sh
git checkout 4ab60d5 -- base/
```

## License

[MIT](LICENSE). `tools/genvol/` is the only code in this repository.

This replaces the Eclipse Public License 2.0 that came with the IBM GenApp
sample formerly vendored here. That licence covered IBM's code, none of which
remains — `base/`, `Changes.md` and `MAINTAINERS.md` were all removed, and the
sample is now referenced upstream instead. IBM's GenApp remains under EPL-2.0 in
[its own repository](https://github.com/cicsdev/cics-genapp).
