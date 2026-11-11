# Spine Magic Builder 1.2.0

`SpineMagicBuilder.exe` is a single-file Windows build of the picker and candidate builder. Download it, double-click, use **Browse** to select an extracted asset folder, then press **Build Candidates**. No Python installation is needed.

The picker now watches for a SpineViewer process that takes foreground focus after an automatic launch and returns keyboard focus to the candidate list. It does not intervene when another application is active. The viewer itself is a separate download; select its exe once with **Viewer exe**.

Verification: four focus/launch tests pass; the packaged exe scans a synthetic candidate set and activates its second candidate through the bundled materializer. The author also confirmed the focus workflow works in an interactive two-window session.

The package contains no game assets or SpineViewer binary.
