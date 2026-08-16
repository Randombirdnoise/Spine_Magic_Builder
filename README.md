# Spine Magic Builder

[English](README.md) · [中文（简体）](README.zh-CN.md)

Spine Magic Builder is a Windows-first reconstruction toolkit for scattered or poorly named Spine assets. It scans a directory tree at byte level, identifies skeletons and atlases, matches texture [...]

The project is designed for extracted asset trees where filenames and extensions cannot be trusted. It supports JSON and binary skeleton discovery, embedded or standalone atlas data, PNG/JPEG/WebP [...]

> Use this tool only with assets you own or are authorized to inspect. No game assets, Spine runtimes, or viewer binaries are included.

## Quick start

### Requirements

- Windows 10 or 11
- Python 3.10 or newer, available as `py` or `python` on `PATH`
- Optional: [SpineViewer](https://github.com/ww-rm/SpineViewer) for visual validation
- Optional GUI enhancements: Pillow and tkinterdnd2

Clone or download this repository, then optionally install the GUI extras:

```powershell
py -3 -m pip install -r requirements-optional.txt
```

The core builder uses only the Python standard library. Pillow adds broader image-dimension and thumbnail support; tkinterdnd2 adds drag-and-drop to the GUI.

### Recommended workflow

1. Drag a source folder onto `Run_SpineMagic_Builder_Candidate_Stage_v3.bat`.
2. Wait for the builder to create a neighboring `Spine_Built-*` directory.
3. Open `Run_SpineCandidatePicker_GUI.bat`.
4. Browse to the built directory containing `_candidates` folders.
5. Select a page and candidate, then press **Activate** or a number key.
6. Point the GUI to `SpineViewer.exe` when prompted and visually validate the set.
7. Mark the selection correct, blacklist it, or skip the page.
8. Press **Finalize Set** once one set looks right, or **Finalize All** after all loaded sets are ready.

The GUI remembers its viewer path and decisions in:

```text
%LOCALAPPDATA%\SpineMagicBuilder\spine_candidate_picker_state.json
```

Set `SPINE_MAGIC_BUILDER_STATE` to use a different state-file location. Set `SPINE_VIEWER_EXE` to define the initial viewer path.

## Included programs

| File | Purpose |
| --- | --- |
| `spine_magic_builder.py` | Core recursive scanner and normalized-set builder. |
| `spine_magic_builder_candidate_materializer_v3.py` | Extended builder with candidate staging and one-candidate materialization. |
| `spine_candidate_picker_gui.py` | Tk GUI for reviewing, activating, finalizing, and recording candidate choices. |
| `Run_SpineMagic_Builder.bat` | Conservative copy-mode builder preset. |
| `Run_SpineMagic_Builder_Candidate_Stage_v3.bat` | Candidate-staging preset optimized for large extracted trees. |
| `Run_SpineCandidatePicker_GUI.bat` | GUI launcher; accepts an optional starting folder. |

## How output is organized

Source files are left in place unless `--move` is explicitly supplied on the command line. The included launchers never use `--move`.

For a selected source folder, output is written next to it in a generated container similar to:

```text
ParentFolder\
├── SourceFolder\
└── Spine_Built-Context_Name\
    └── SourceFolder\
        └── normalized_spine_set\
            ├── normalized_spine_set.skel (or .json)
            ├── normalized_spine_set.atlas
            ├── texture pages
            └── _candidates\
```

Candidate activation preserves the previously active page in `_materialized_history` before placing the new candidate.

## Launcher details

### Standard builder

`Run_SpineMagic_Builder.bat` uses copy mode and a moderate atlas-match threshold. Drag a folder onto it or call:

```bat
Run_SpineMagic_Builder.bat "D:\ExtractedGame\assets"
```

### Candidate-stage builder

`Run_SpineMagic_Builder_Candidate_Stage_v3.bat` isolates immediate child folders as entities, ranks ambiguous same-dimension textures, and stages all candidates. It requests symlinks to avoid dupl[...]

Use `--stage-dim-candidates-limit N` directly from Python when unlimited staging would produce too many candidates.

### Candidate picker GUI

Run without an argument and browse to a folder, or provide a starting path:

```bat
Run_SpineCandidatePicker_GUI.bat "D:\ExtractedGame\Spine_Built-Example"
```

Useful keys:

| Key | Action |
| --- | --- |
| `1`-`9` | Activate candidate rank 1-9 |
| `Enter` | Activate the selected candidate |
| `Left` / `Right` | Previous / next candidate |
| `Ctrl+Left` / `Ctrl+Right` | Previous / next atlas page |
| `C` | Mark selected candidate correct |
| `B` | Blacklist selected candidate |
| `S` | Skip current page |
| `F5` | Launch SpineViewer |

Use the **Viewer exe** button if SpineViewer is not found automatically. The automatic search checks the repository folder, a `SpineViewer` subfolder, and the repository's parent folder. SpineVie[...]

Use **Finalize Set** after visual validation to make the selected texture pages final. Finalize uses **Mark Correct** choices first, then falls back to the last activated candidate for pages that were activated but not explicitly marked. **Finalize All** applies the same rules to every loaded set that has at least one selected or activated page, with a confirmation dialog before anything is changed. The default finalize settings move selected page images into the built set and delete `_candidates`. If a staged candidate is a symlink, move mode dereferences it into a real local file so the finished set does not depend on the staged symlink target. Other finalize modes are copy, hardlink, and symlink; disable **Delete _candidates** to keep staged candidates for audit or later adjustment.

Blacklist and used-image entries are stored per page and globally by candidate identity, cleaned staged filename, and trailing long numeric suffix such as `_00001`. Rejecting or accepting the same source image hides it in later folders as you continue reviewing, and candidate counts show the visible total when **Hide blacklisted** is enabled. **Hide blacklisted** is enabled by default and can be unchecked to review hidden candidates. If an image is already marked correct elsewhere, a later blacklist stays local to the current page.

Use **Preview px** to resize the thumbnail area when a candidate needs closer inspection. The preview size is saved with the other GUI settings.

## Command-line usage

Core builder:

```powershell
py -3 spine_magic_builder.py --root "D:\ExtractedGame\assets" --dims-fallback --prefer-nearby-textures
```

Stage ambiguous dimension matches:

```powershell
py -3 spine_magic_builder_candidate_materializer_v3.py `
  --root "D:\ExtractedGame\assets" `
  --dims-fallback `
  --stage-dim-candidates `
  --stage-dim-candidates-limit 250
```

Activate one staged candidate without the GUI:

```powershell
py -3 spine_magic_builder_candidate_materializer_v3.py `
  --materialize-built-set "D:\Path\To\OneBuiltSet" `
  --materialize-page 1 `
  --materialize-candidate 3 `
  --link-mode copy
```

Run either builder with `--help` for the complete option list. Useful advanced switches include `--explain-match`, `--top-n`, `--entity-mode`, `--rewrite-pages-to-match-source`, and `--dedupe-tex[...]

## Safety notes

- Default CLI behavior copies source textures; the standard launcher explicitly uses copy mode.
- The included launchers never pass `--move`.
- Symlink and hardlink modes save disk space but link generated output to original texture data. Use copy mode if the built set will be edited.
- `--aggressive-atlas` and `--dims-fallback` improve recovery from damaged naming but can produce false matches. Validate ambiguous results visually.
- Candidate staging may consume substantial disk space when linking is unavailable. Set a finite candidate limit if needed.
- Generated state, game assets, candidate folders, and build output are excluded by `.gitignore`.

## Troubleshooting

**Python was not found**  
Install Python 3.10+ and enable the installer option to add Python to `PATH`, then open a new terminal.

**The GUI opens but thumbnails are limited**  
Install `requirements-optional.txt`. The GUI falls back to Tk's built-in PNG loader when Pillow is absent.

**Drag-and-drop does nothing**  
Install `tkinterdnd2`, or use the GUI's Browse button.

**Symlink creation fails on Windows**  
Enable Windows Developer Mode, run with suitable privileges, or use `--link-mode copy`. The candidate builder already falls back to a hardlink and then a copy.

**SpineViewer is missing**  
Download a release from the upstream [SpineViewer repository](https://github.com/ww-rm/SpineViewer), then select `SpineViewer.exe` with the GUI's **Viewer exe** button.

## Acknowledgements

A very special thank-you to [ww-rm/SpineViewer](https://github.com/ww-rm/SpineViewer) and its contributors. SpineViewer is the external visual-validation component used by the GUI workflow and ma[...]

SpineViewer is not bundled with or relicensed by this project. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for details.

## License

Spine Magic Builder's original code and documentation are released under the [MIT License](LICENSE). Third-party programs and assets are governed by their own terms.
