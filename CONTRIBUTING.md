# Contributing

Bug reports and focused pull requests are welcome.

When reporting a matching problem, include:

- the command or launcher used;
- the relevant `[OK]`, `[MISS]`, `[WARN]`, or `[ERR]` output;
- a minimal sanitized folder layout;
- whether the skeleton is JSON or binary and whether the atlas is single-page or multi-page.

Do not attach copyrighted game assets unless you have permission to redistribute them. A small synthetic fixture or redacted metadata sample is preferred.

Before submitting code, run:

```powershell
python -m compileall -q .
python spine_magic_builder.py --help
python spine_magic_builder_candidate_materializer_v3.py --help
python spine_candidate_picker_gui.py --help
```
