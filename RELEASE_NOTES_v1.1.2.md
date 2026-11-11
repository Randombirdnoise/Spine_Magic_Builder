# v1.1.2 — GUI finalize workflow & picker stability fixes

This release adds a GUI "Finalize" workflow for candidate staging and SpineViewer validation, plus a series of performance and stability fixes to the candidate picker (PR #2).

Commit: `37c737243956e0df12e694536ef611277e4f164c`
Commit URL: https://github.com/Randombirdnoise/Spine_Magic_Builder/commit/37c737243956e0df12e694536ef611277e4f164c

Highlights

- Add GUI Finalize workflow and "Finalize All" (confirmation + scan-root refresh) so validated candidate selections become final textures in the built set.
- Move GUI scanning and candidate thumbnail loading off the Tk event loop to keep the picker responsive on large candidate sets.
- Added stale-result guards and prevented overlapping candidate activation jobs to avoid UI races.
- Optimized candidate filtering and navigation, reduced duplicate preview refreshes.
- Rolling debug/timing log for scan, preview, activation, blacklist, and finalize operations.
- Prefer `pythonw.exe` before `pyw.exe -3` in the Windows GUI launcher for more reliable GUI startup.

Files changed (summary)

- `spine_candidate_picker_gui.py`: added finalize UI and logic, caching, background scanning and thumbnail loading, debug log, and robustness fixes.
- `Run_SpineCandidatePicker_GUI.bat`: prefer `pythonw.exe` when available before falling back to `pyw.exe -3`.
- `README.md` / `CHANGELOG.md`: documented the finalize UI and picker improvements.

Upgrade / migration notes

- Finalize can operate in different modes: move (default), copy, hardlink, or symlink. When using `move` mode, staged candidate files are relocated into the final set and `_candidates` may be removed. If you use `symlink` mode and also choose to delete `_candidates`, ensure source images will remain available or prefer `move`/`copy`.

Install / Download

- The default branch `main` contains this release snapshot. Cloning the repo or using the Code → Download ZIP on the repository will provide this release state.

How to create a Git tag + GitHub release (copy-paste)

1) Create an annotated tag and push it to origin:

```bash
# fetch latest and tag the main commit
git fetch origin
git checkout main
git pull origin main
# create annotated tag pointing at the squash commit
git tag -a v1.1.2 37c737243956e0df12e694536ef611277e4f164c -m "v1.1.2 — Add GUI finalize workflow for candidate staging and SpineViewer validation (#2)"
# push tag
git push origin v1.1.2
```

2) Create a GitHub release using gh (recommended):

```bash
# from a machine with gh CLI authenticated
gh release create v1.1.2 37c737243956e0df12e694536ef611277e4f164c --title "v1.1.2 — GUI finalize workflow & picker stability fixes" --notes-file RELEASE_NOTES_v1.1.2.md
```

3) Or create the release via the web UI:
- Go to: https://github.com/Randombirdnoise/Spine_Magic_Builder/releases
- Click "Draft a new release"
- Tag version: `v1.1.2`
- Target: `main` (or paste commit SHA `37c737243...`)
- Title: `v1.1.2 — GUI finalize workflow & picker stability fixes`
- Paste this release notes content into the description and publish the release.

If you want, I can also: create the annotated tag and push it for you (I need permission and an authenticated token / GitHub integration), or create the release via the GitHub API if you provide a repo-scoped token. I cannot create a GitHub release from here without credentials.

---

Thank you — if you'd like, I can also draft a short social/media blurb or a one-paragraph announcement you can paste into Discord/Twitter/Slack.
