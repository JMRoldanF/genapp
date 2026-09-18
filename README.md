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

## License

[MIT](LICENSE). `tools/genvol/` is the only code in this repository, and all of
it is original — no third-party source is vendored here.
