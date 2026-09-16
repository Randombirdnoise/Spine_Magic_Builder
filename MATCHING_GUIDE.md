# Skeleton / atlas recovery

Use either existing BAT launcher as before. Keep `spine_atlas_matching.py` beside both builder scripts: it is their shared matching engine.

Each skeleton is compared with every atlas in the selected tree. A skeleton is never consumed by an earlier atlas choice, and an atlas can serve any number of skeletons. The first choice is exported as a normal set. Every alternative remains in the report for on-demand review. Building an alternative creates a complete set under `_atlas_candidates` using the same original skeleton. There is no atlas candidate cap. Texture candidate limits retain their existing meaning, including `0 = unlimited`.

## Evidence and uncertainty

- JSON: use actual region/mesh attachment paths, including explicit `path`/`name` overrides and numbered sequences. Bones, slot names, skin names, clipping and bounding boxes do not count as texture requirements. A complete JSON path match ranks ahead of a partial one.
- Binary 3.8: parse the header and shared-string table, keeping later bone/animation names out of strong region evidence. Other versions and unrecognized headers use exact UTF-8 strings with their Spine varint length prefix. `body` inside `body_2` is only weak evidence. This is **not a full attachment/animation parser**: shared strings can include non-rendering names, and fallback bytes can resemble an encoded string.
- Count each distinct atlas region once. Weight uncommon region names more strongly than names shared by many atlases. Names and directory proximity break evidence ties; input enumeration order does not.
- Embedded atlas metadata takes priority over external guesses.
- Preserve ambiguous and weak alternatives instead of calling a high hit count proof of correctness. Validate appearance in SpineViewer.

These rules follow Spine's [JSON attachment lookups](https://esotericsoftware.com/spine-json-format) and [binary string encoding](https://esotericsoftware.com/spine-binary-format). The ranking policy is this tool's heuristic, not an official Spine validator.

## Output and audit

Each run creates a new `run-...` folder under the usual `Spine_Built-.../SourceFolder` location. The console prints its full path after `[output]`. Old runs and the original assets stay in place. The GUI's **Build Candidates** command opens the new run after building.

`atlas_match_report.jsonl` contains a header, one record per skeleton, and a completion summary. Each skeleton record lists **every evaluated atlas**, including zero-hit candidates, exact/weak region evidence, missing JSON paths, scores, selected status, primary/alternative exports and their output paths or errors. Reports are streamed and flushed after each skeleton; if a run is interrupted, the absence of a summary marks it incomplete. `selected` means included by the export policy, not verified correct. `provisional` means a first guess; `unique-complete-json` means only one atlas contains all known JSON texture paths, not a rendering guarantee.

In the picker, click **Atlas Matches** and choose `atlas_match_report.jsonl` (or open the run folder first). Select a skeleton to see **all** evaluated atlases, their exact/weak hits, missing JSON paths, scores and detailed evidence. **Build selected atlas pair** materializes just that pair, then **Open built pair in SpineViewer** opens it for visual validation. There is no blacklist or used-skeleton suppression in atlas review. Source skeleton/atlas fingerprints are checked before building so a stale report cannot silently use changed data.

The review window indexes report offsets rather than loading the full pair matrix into memory. Indexing and building run in workers; duplicate build actions are blocked. The original texture picker also discovers `_candidates` within materialized alternative sets recursively.

## Large piles: audit before materializing

With shared names, hundreds of skeletons can plausibly match hundreds of atlases. Default `--atlas-alternatives deferred` keeps every alternative in the report without making all those copies. For a large uncertain corpus, inspect a report first:

```powershell
python spine_magic_builder_candidate_materializer_v3.py --root "D:\Assets" --match-report-only --top-n 0
```

This scans source assets read-only and writes only the report to a new output run. It skips texture indexing and placement. Then run the usual candidate-stage BAT, which prefers links, or choose a policy explicitly:

```powershell
# Export every pair with region/string or page evidence immediately.
python spine_magic_builder_candidate_materializer_v3.py --root "D:\Assets" --atlas-candidates plausible --atlas-alternatives export --link-mode hardlink --dims-fallback --stage-dim-candidates --stage-dim-candidates-limit 0

# Exhaustive Cartesian product, including zero-evidence pairs.
python spine_magic_builder_candidate_materializer_v3.py --root "D:\Assets" --atlas-candidates all --atlas-alternatives export --link-mode hardlink

# Export only the first choice; still report every evaluated atlas.
python spine_magic_builder.py --root "D:\Assets" --atlas-candidates best

# Build any individual pair directly from its report, without the GUI.
python spine_magic_builder_candidate_materializer_v3.py --materialize-atlas-report "D:\Built\run-123\atlas_match_report.jsonl" --materialize-skeleton "D:\Assets\actor.skel" --materialize-atlas "D:\Assets\candidate.atlas"
```

`plausible` includes weak binary substrings for review but those cannot satisfy `--min-hits`. JSON candidates need attachment/path or page evidence; `all` is available for damaged or unsupported naming. These policies choose which alternatives to export when `--atlas-alternatives export` is used; the report and review window always retain every evaluated atlas. If nothing meets `--min-hits`, the normal output contains a normalized skeleton without an external atlas, and alternatives remain available. `--aggressive-atlas` explicitly restores an unsupported first guess; launchers no longer enable it. `--top-n` only controls printed diagnostics.

Default scanning is global across the selected tree. Use `--entity-mode childdirs` only for known independent child folders: it deliberately excludes cross-child matches and loose files directly under the selected root. The report covers the chosen scan scope.

`--move` is restricted to `--atlas-candidates best` to keep shared sources available during alternative recovery. The launchers do not move source assets. `--allow-reuse-textures` now correctly opts into reusing a texture for multiple pages within one atlas; different skeleton/atlas sets can always reuse source textures.

## Validation

```powershell
python -B -m unittest discover -s tests -v
```

The suite creates synthetic assets, verifies both builders end to end, checks source preservation, shared-atlas sets, ties, substring traps, explicit JSON paths, UTF-8 strings, embedded metadata, rescans and the 500-skeleton/501-atlas ranking case. It does not substitute for visual validation on a real game's assets.
