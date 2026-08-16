#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import math
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
import tkinter as tk
from tkinter import ttk


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MATERIALIZER = SCRIPT_DIR / "spine_magic_builder_candidate_materializer_v3.py"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
FINALIZE_MODES = ("move", "copy", "hardlink", "symlink")


def default_viewer_path() -> Path:
    override = os.environ.get("SPINE_VIEWER_EXE", "").strip()
    if override:
        return Path(override).expanduser()

    candidates = (
        SCRIPT_DIR / "SpineViewer.exe",
        SCRIPT_DIR / "SpineViewer" / "SpineViewer.exe",
        SCRIPT_DIR.parent / "SpineViewer.exe",
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


def state_path() -> Path:
    override = os.environ.get("SPINE_MAGIC_BUILDER_STATE", "").strip()
    if override:
        return Path(override).expanduser()

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / ".spine_magic_builder"
    return base / "SpineMagicBuilder" / "spine_candidate_picker_state.json"


DEFAULT_VIEWER = default_viewer_path()
STATE_PATH = state_path()


@dataclass
class Candidate:
    rank: int
    score: int | None
    file_path: Path
    source_path: str = ""
    note: str = ""


@dataclass
class PageCandidates:
    page_index: int
    atlas_page: str
    requested_size: str = ""
    folder: Path | None = None
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class SpineSet:
    folder: Path
    atlas: Path | None
    skeletons: list[Path]
    pages: list[PageCandidates]


def read_text(path: Path, max_bytes: int | None = None) -> str:
    try:
        if max_bytes is None:
            return path.read_text(encoding="utf-8", errors="replace")
        with path.open("rb") as f:
            return f.read(max_bytes).decode("utf-8", errors="replace")
    except OSError:
        return ""


def image_size(path: Path) -> tuple[int | None, int | None]:
    data = b""
    try:
        with path.open("rb") as f:
            data = f.read(64)
    except OSError:
        return None, None
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return None, None


def parse_atlas_pages(atlas_path: Path) -> list[dict]:
    text = read_text(atlas_path)
    pages = []
    blocks = re.split(r"\r?\n\s*\r?\n", (text or "").strip())
    for sec in blocks:
        lines = sec.splitlines()
        if not lines:
            continue
        i = 0
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            continue
        name = Path(lines[i].strip()).name
        width = height = None
        i += 1
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            m = re.match(r"size\s*:\s*(\d+)\s*,\s*(\d+)", line, re.I)
            if m:
                width, height = int(m.group(1)), int(m.group(2))
                i += 1
                continue
            if re.match(r"^(format|filter|repeat|pma)\s*:", line, re.I):
                i += 1
                continue
            break
        pages.append({"name": name, "size": f"{width}x{height}" if width and height else ""})
    return pages


def parse_manifest(cand_dir: Path) -> PageCandidates:
    manifest = cand_dir / "_manifest.txt"
    text = read_text(manifest)
    page_index = 0
    atlas_page = ""
    requested_size = ""
    candidates: list[Candidate] = []

    for line in text.splitlines():
        if line.startswith("page_index:"):
            page_index = int(line.split(":", 1)[1].strip())
        elif line.startswith("atlas_page:"):
            atlas_page = line.split(":", 1)[1].strip()
        elif line.startswith("requested_size:"):
            requested_size = line.split(":", 1)[1].strip()
        else:
            m = re.match(r"\s*(\d{4})\s+score=\s*(-?\d+)\s+src=(.*?)(?:\s+\[(.*)\])?\s*$", line)
            if not m:
                continue
            rank = int(m.group(1))
            score = int(m.group(2))
            src = m.group(3).strip()
            note = (m.group(4) or "").strip()
            file_match = sorted(cand_dir.glob(f"{rank:04d}_*"))
            if file_match:
                candidates.append(Candidate(rank=rank, score=score, file_path=file_match[0], source_path=src, note=note))

    if not page_index:
        m = re.match(r"page(\d+)_", cand_dir.name, re.I)
        page_index = int(m.group(1)) if m else 0
    if not atlas_page:
        atlas_page = cand_dir.name
    if not candidates:
        for file_path in sorted(cand_dir.iterdir()):
            if file_path.is_file() and file_path.name != "_manifest.txt" and file_path.suffix.lower() in IMAGE_EXTS:
                m = re.match(r"(\d{4})_score(-?\d+)_", file_path.name)
                if not m:
                    continue
                candidates.append(Candidate(rank=int(m.group(1)), score=int(m.group(2)), file_path=file_path))

    return PageCandidates(page_index=page_index, atlas_page=atlas_page, requested_size=requested_size, folder=cand_dir, candidates=candidates)


def discover_spine_sets(root: Path) -> list[SpineSet]:
    sets = []
    for cand_root in sorted(root.rglob("_candidates"), key=lambda p: str(p).lower()):
        if not cand_root.is_dir():
            continue
        set_dir = cand_root.parent
        atlases = sorted(set_dir.glob("*.atlas"), key=lambda p: p.name.lower())
        skeletons = sorted(
            [p for p in set_dir.iterdir() if p.is_file() and p.suffix.lower() in {".skel", ".json"}],
            key=lambda p: p.name.lower(),
        )
        pages = [parse_manifest(p) for p in sorted(cand_root.iterdir(), key=lambda p: p.name.lower()) if p.is_dir()]
        pages = [p for p in pages if p.page_index and p.candidates]
        if pages:
            sets.append(SpineSet(folder=set_dir, atlas=atlases[0] if atlases else None, skeletons=skeletons, pages=pages))
    return sets


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"correct": {}, "blacklist": {}, "skipped": {}, "settings": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    tmp.replace(STATE_PATH)


def set_key(spine_set: SpineSet, page: PageCandidates) -> str:
    return f"{spine_set.folder.resolve()}|page{page.page_index:02d}|{page.atlas_page}"


def path_key(path: Path | str) -> str:
    try:
        return str(Path(path).expanduser().resolve()).lower()
    except OSError:
        return str(path).strip().lower()


def candidate_identity(candidate: Candidate) -> str:
    raw = (candidate.source_path or "").strip().strip('"')
    if raw:
        source = Path(raw)
        if source.is_absolute() or source.exists():
            return path_key(source)
    return path_key(candidate.file_path)


def _candidate_name_aliases(raw: str) -> set[str]:
    name = Path(str(raw).strip().strip('"')).name.lower()
    if not name:
        return set()
    # Staged files are named like 0001_score123_original_00001.png.
    clean_name = re.sub(r"^\d{4}_score-?\d+_", "", name)
    stem = Path(clean_name).stem
    aliases = {f"name:{clean_name}", f"stem:{stem}"}
    m = re.search(r"(?:^|[_\-.])(\d{5,})$", stem)
    if m:
        aliases.add(f"suffix:{m.group(1)}")
    return aliases


def candidate_aliases(candidate: Candidate) -> set[str]:
    aliases = {candidate_identity(candidate), path_key(candidate.file_path)}
    raw_source = (candidate.source_path or "").strip().strip('"')
    if raw_source:
        aliases.add(path_key(raw_source))
        aliases.update(_candidate_name_aliases(raw_source))
    aliases.update(_candidate_name_aliases(str(candidate.file_path)))
    return {alias for alias in aliases if alias}


def state_entry_identities(entry: dict) -> set[str]:
    identities = set()
    for alias in entry.get("aliases", []) or []:
        raw = str(alias).strip()
        if raw:
            identities.add(raw)
    for key in ("source_path", "candidate_file"):
        raw = str(entry.get(key) or "").strip().strip('"')
        if raw:
            identities.add(path_key(raw))
            identities.update(_candidate_name_aliases(raw))
    return identities


def remove_existing(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def relpath(src: Path, dst_parent: Path) -> str:
    try:
        return os.path.relpath(str(src), start=str(dst_parent))
    except OSError:
        return str(src)


def same_file(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return path_key(left) == path_key(right)


def place_final_texture(src: Path, target: Path, mode: str) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode not in FINALIZE_MODES:
        raise ValueError(f"Unknown finalize mode: {mode}")

    if not src.exists() and not src.is_symlink():
        raise FileNotFoundError(f"Missing selected candidate: {src}")

    tmp = target.with_name(f".{target.name}.finalize_tmp")
    remove_existing(tmp)

    if mode == "move":
        if target.exists() and same_file(src, target) and not target.is_symlink() and not src.is_symlink():
            return "already local"
        if src.is_symlink():
            resolved = src.resolve(strict=True)
            shutil.copy2(resolved, tmp)
            os.replace(tmp, target)
            try:
                src.unlink()
            except OSError:
                pass
            return "move (dereferenced symlink)"
        shutil.move(str(src), str(tmp))
        os.replace(tmp, target)
        return "move"

    resolved_src = src.resolve(strict=True) if src.is_symlink() else src
    if mode == "copy":
        if target.exists() and same_file(resolved_src, target) and not target.is_symlink():
            return "already local"
        shutil.copy2(resolved_src, tmp)
    elif mode == "hardlink":
        os.link(resolved_src, tmp)
    elif mode == "symlink":
        os.symlink(relpath(resolved_src, target.parent), tmp)

    os.replace(tmp, target)
    return mode


class CandidatePickerApp:
    def __init__(self, root: tk.Tk, start_path: Path | None = None):
        self.root = root
        self.root.title("Spine Candidate Picker")
        self.root.geometry("1280x860")
        self.state = load_state()
        self.spine_sets: list[SpineSet] = []
        self.current_set_index = 0
        self.current_page_index = 0
        self.current_candidate_rank: int | None = None
        self.viewer_proc: subprocess.Popen | None = None
        self.materializer = Path(self.state.get("settings", {}).get("materializer") or DEFAULT_MATERIALIZER)
        self.viewer = Path(self.state.get("settings", {}).get("viewer") or DEFAULT_VIEWER)
        self.link_mode = tk.StringVar(value=self.state.get("settings", {}).get("link_mode", "symlink"))
        finalize_mode = self.state.get("settings", {}).get("finalize_mode", "move")
        if finalize_mode not in FINALIZE_MODES:
            finalize_mode = "move"
        self.finalize_mode = tk.StringVar(value=finalize_mode)
        self.delete_candidates_after_finalize = tk.BooleanVar(value=self.state.get("settings", {}).get("delete_candidates_after_finalize", True))
        self.hide_blacklisted = tk.BooleanVar(value=self.state.get("settings", {}).get("hide_blacklisted", True))
        try:
            preview_height = int(self.state.get("settings", {}).get("preview_height", 320))
        except (TypeError, ValueError):
            preview_height = 320
        self.preview_height = tk.IntVar(value=preview_height)
        self.stage_limit = tk.StringVar(value=str(self.state.get("settings", {}).get("stage_limit", 150)))
        self.auto_viewer = tk.BooleanVar(value=self.state.get("settings", {}).get("auto_viewer", True))
        self.restart_viewer = tk.BooleanVar(value=self.state.get("settings", {}).get("restart_viewer", True))
        self.status = tk.StringVar(value="Drop, browse, or paste a built folder containing _candidates.")
        self.path_var = tk.StringVar()
        self.thumbnail_image = None
        self.thumbnail_path: Path | None = None

        self._build_ui()
        self._bind_keys()
        self._try_enable_drag_drop()

        if start_path:
            self.scan_root(start_path)

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Folder").pack(side=tk.LEFT)
        entry = ttk.Entry(top, textvariable=self.path_var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        entry.bind("<Return>", lambda _e: self.scan_root(Path(self.path_var.get().strip('"'))))
        ttk.Button(top, text="Browse", command=self.browse_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Scan", command=lambda: self.scan_root(Path(self.path_var.get().strip('"')))).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Build Candidates", command=self.build_candidates).pack(side=tk.LEFT, padx=2)

        opts = ttk.Frame(self.root, padding=(8, 0, 8, 6))
        opts.pack(fill=tk.X)
        ttk.Checkbutton(opts, text="Auto viewer", variable=self.auto_viewer, command=self.save_settings).pack(side=tk.LEFT)
        ttk.Checkbutton(opts, text="Restart launched viewer", variable=self.restart_viewer, command=self.save_settings).pack(side=tk.LEFT, padx=10)
        ttk.Label(opts, text="Link mode").pack(side=tk.LEFT)
        ttk.Combobox(opts, textvariable=self.link_mode, values=("symlink", "hardlink", "copy"), width=9, state="readonly").pack(side=tk.LEFT, padx=6)
        ttk.Label(opts, text="Candidate limit").pack(side=tk.LEFT)
        stage_limit_entry = ttk.Entry(opts, textvariable=self.stage_limit, width=6)
        stage_limit_entry.pack(side=tk.LEFT, padx=6)
        stage_limit_entry.bind("<FocusOut>", lambda _e: self.save_settings())
        stage_limit_entry.bind("<Return>", lambda _e: self.save_settings())
        ttk.Button(opts, text="Viewer exe", command=self.pick_viewer).pack(side=tk.LEFT, padx=2)
        ttk.Button(opts, text="Materializer", command=self.pick_materializer).pack(side=tk.LEFT, padx=2)
        ttk.Button(opts, text="Open Set Folder", command=self.open_current_folder).pack(side=tk.RIGHT)

        final_opts = ttk.Frame(self.root, padding=(8, 0, 8, 6))
        final_opts.pack(fill=tk.X)
        ttk.Label(final_opts, text="Finalize mode").pack(side=tk.LEFT)
        finalize_combo = ttk.Combobox(final_opts, textvariable=self.finalize_mode, values=FINALIZE_MODES, width=9, state="readonly")
        finalize_combo.pack(side=tk.LEFT, padx=6)
        finalize_combo.bind("<<ComboboxSelected>>", lambda _e: self.save_settings())
        ttk.Checkbutton(final_opts, text="Delete _candidates", variable=self.delete_candidates_after_finalize, command=self.save_settings).pack(side=tk.LEFT, padx=8)
        ttk.Checkbutton(final_opts, text="Hide blacklisted", variable=self.hide_blacklisted, command=self.on_blacklist_filter_change).pack(side=tk.LEFT, padx=8)
        ttk.Label(final_opts, text="Preview px").pack(side=tk.LEFT, padx=(14, 2))
        preview_spin = tk.Spinbox(final_opts, from_=120, to=1000, increment=20, textvariable=self.preview_height, width=5, command=self.apply_preview_height)
        preview_spin.pack(side=tk.LEFT)
        preview_spin.bind("<FocusOut>", lambda _e: self.apply_preview_height())
        preview_spin.bind("<Return>", lambda _e: self.apply_preview_height())
        ttk.Button(final_opts, text="Finalize All", command=self.finalize_all_sets).pack(side=tk.RIGHT, padx=2)
        ttk.Button(final_opts, text="Finalize Set", command=self.finalize_current_set).pack(side=tk.RIGHT, padx=2)

        main = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        left = ttk.Frame(main)
        main.add(left, weight=1)
        ttk.Label(left, text="Spine Sets").pack(anchor=tk.W)
        self.set_tree = ttk.Treeview(left, columns=("pages", "skeleton", "atlas"), show="headings", height=12)
        self.set_tree.heading("pages", text="Pages")
        self.set_tree.heading("skeleton", text="Skeleton")
        self.set_tree.heading("atlas", text="Atlas")
        self.set_tree.column("pages", width=50, stretch=False)
        self.set_tree.column("skeleton", width=220)
        self.set_tree.column("atlas", width=220)
        self.set_tree.pack(fill=tk.BOTH, expand=True)
        self.set_tree.bind("<<TreeviewSelect>>", self.on_set_select)

        self.page_tree = ttk.Treeview(left, columns=("page", "size", "count", "choice"), show="headings", height=8)
        for col, text, width in (("page", "Atlas Page", 180), ("size", "Size", 80), ("count", "Candidates", 80), ("choice", "Cached", 90)):
            self.page_tree.heading(col, text=text)
            self.page_tree.column(col, width=width)
        self.page_tree.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.page_tree.bind("<<TreeviewSelect>>", self.on_page_select)

        right = ttk.Frame(main)
        main.add(right, weight=2)
        ttk.Label(right, text="Candidates").pack(anchor=tk.W)
        self.cand_tree = ttk.Treeview(right, columns=("rank", "score", "file", "source", "note"), show="headings", height=18)
        for col, text, width in (
            ("rank", "Rank", 58),
            ("score", "Score", 70),
            ("file", "Candidate File", 260),
            ("source", "Source", 420),
            ("note", "Note", 210),
        ):
            self.cand_tree.heading(col, text=text)
            self.cand_tree.column(col, width=width)
        self.cand_tree.pack(fill=tk.BOTH, expand=True)
        self.cand_tree.bind("<<TreeviewSelect>>", self.on_candidate_select)
        self.cand_tree.bind("<Double-1>", lambda _e: self.materialize_selected())

        controls = ttk.Frame(right, padding=(0, 8, 0, 0))
        controls.pack(fill=tk.X)
        ttk.Button(controls, text="Activate", command=self.materialize_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls, text="Prev", command=self.previous_candidate).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls, text="Next", command=self.next_candidate).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls, text="Mark Correct", command=self.mark_correct).pack(side=tk.LEFT, padx=14)
        ttk.Button(controls, text="Blacklist", command=self.blacklist_candidate).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls, text="Skip Page", command=self.skip_page).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls, text="Launch Viewer", command=self.launch_viewer).pack(side=tk.RIGHT, padx=2)

        detail_pane = ttk.PanedWindow(right, orient=tk.HORIZONTAL)
        detail_pane.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        preview_frame = ttk.Frame(detail_pane)
        detail_pane.add(preview_frame, weight=2)
        ttk.Label(preview_frame, text="Thumbnail").pack(anchor=tk.W)
        self.thumbnail_canvas = tk.Canvas(preview_frame, height=self.normalized_preview_height(), bg="#242424", highlightthickness=1, highlightbackground="#555555")
        self.thumbnail_canvas.pack(fill=tk.BOTH, expand=True)
        self.thumbnail_canvas.bind("<Configure>", lambda _e: self.update_thumbnail())
        ttk.Label(preview_frame, text="Preview Metadata").pack(anchor=tk.W, pady=(8, 0))
        self.preview = tk.Text(preview_frame, height=7, wrap=tk.NONE)
        self.preview.pack(fill=tk.BOTH, expand=True)
        log_frame = ttk.Frame(detail_pane)
        detail_pane.add(log_frame, weight=1)
        ttk.Label(log_frame, text="Log").pack(anchor=tk.W)
        self.log = tk.Text(log_frame, height=8, wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True)

        bottom = ttk.Label(self.root, textvariable=self.status, anchor=tk.W, padding=(8, 4))
        bottom.pack(fill=tk.X)

    def _bind_keys(self):
        for i in range(1, 10):
            self.root.bind(str(i), lambda _e, n=i: self.activate_rank(n))
        self.root.bind("<Right>", lambda _e: self.next_candidate())
        self.root.bind("<Left>", lambda _e: self.previous_candidate())
        self.root.bind("<Control-Right>", lambda _e: self.next_page())
        self.root.bind("<Control-Left>", lambda _e: self.previous_page())
        self.root.bind("<Return>", lambda _e: self.materialize_selected())
        self.root.bind("c", lambda _e: self.mark_correct())
        self.root.bind("b", lambda _e: self.blacklist_candidate())
        self.root.bind("s", lambda _e: self.skip_page())
        self.root.bind("<F5>", lambda _e: self.launch_viewer())

    def _try_enable_drag_drop(self):
        try:
            from tkinterdnd2 import DND_FILES  # type: ignore
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self.on_drop)
            self.status.set("Drag/drop enabled. Drop a built folder or scan root.")
        except Exception:
            pass

    def log_line(self, line: str):
        self.log.insert(tk.END, line.rstrip() + "\n")
        self.log.see(tk.END)
        self.status.set(line.rstrip())

    def save_settings(self):
        self.state.setdefault("settings", {})
        self.state["settings"].update(
            {
                "materializer": str(self.materializer),
                "viewer": str(self.viewer),
                "link_mode": self.link_mode.get(),
                "finalize_mode": self.finalize_mode.get(),
                "delete_candidates_after_finalize": bool(self.delete_candidates_after_finalize.get()),
                "hide_blacklisted": bool(self.hide_blacklisted.get()),
                "preview_height": self.normalized_preview_height(),
                "stage_limit": self.normalized_stage_limit(),
                "auto_viewer": bool(self.auto_viewer.get()),
                "restart_viewer": bool(self.restart_viewer.get()),
            }
        )
        save_state(self.state)

    def normalized_stage_limit(self) -> int:
        raw = self.stage_limit.get().strip()
        try:
            value = int(raw)
        except ValueError:
            value = 150
        if value < 0:
            value = 0
        self.stage_limit.set(str(value))
        return value

    def normalized_preview_height(self) -> int:
        try:
            value = int(self.preview_height.get())
        except (tk.TclError, ValueError):
            value = 320
        value = max(120, min(value, 1000))
        self.preview_height.set(value)
        return value

    def apply_preview_height(self):
        value = self.normalized_preview_height()
        if hasattr(self, "thumbnail_canvas"):
            self.thumbnail_canvas.configure(height=value)
            self.update_thumbnail()
        self.save_settings()

    def on_blacklist_filter_change(self):
        self.save_settings()
        self.refresh_candidates()
        self.select_candidate_by_index(0)

    def browse_folder(self):
        folder = filedialog.askdirectory(title="Select built Spine folder or scan root")
        if folder:
            self.scan_root(Path(folder))

    def pick_viewer(self):
        path = filedialog.askopenfilename(title="Select SpineViewer.exe", filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
        if path:
            self.viewer = Path(path)
            self.save_settings()
            self.log_line(f"[OK] viewer: {self.viewer}")

    def pick_materializer(self):
        path = filedialog.askopenfilename(title="Select materializer script", filetypes=[("Python", "*.py"), ("All files", "*.*")])
        if path:
            self.materializer = Path(path)
            self.save_settings()
            self.log_line(f"[OK] materializer: {self.materializer}")

    def on_drop(self, event):
        raw = event.data.strip()
        paths = self.root.tk.splitlist(raw)
        if paths:
            self.scan_root(Path(paths[0]))

    def scan_root(self, path: Path):
        if not path:
            return
        path = path.expanduser()
        if path.is_file():
            path = path.parent
        if not path.exists():
            messagebox.showerror("Missing folder", str(path))
            return
        self.path_var.set(str(path))
        self.log_line(f"[scan] {path}")
        self.spine_sets = discover_spine_sets(path)
        self.current_set_index = 0
        self.current_page_index = 0
        self.refresh_sets()
        if self.spine_sets:
            self.select_set(0)
            self.log_line(f"[OK] found {len(self.spine_sets)} built set(s) with candidates")
        else:
            self.log_line("[MISS] no built sets with _candidates found. Use Build Candidates first if this is raw source.")

    def build_candidates(self):
        path = Path(self.path_var.get().strip('"'))
        if not path.exists():
            messagebox.showerror("Missing folder", "Select a source folder first.")
            return
        if not self.materializer.exists():
            messagebox.showerror("Missing materializer", str(self.materializer))
            return
        stage_limit = self.normalized_stage_limit()
        self.save_settings()
        cmd = [
            sys.executable,
            str(self.materializer),
            "--root",
            str(path),
            "--dims-fallback",
            "--min-hits",
            "40",
            "--prefer-nearby-textures",
            "--prefer-consistent-texture-dir",
            "--aggressive-atlas",
            "--rewrite-pages-to-match-source",
            "--entity-mode",
            "childdirs",
            "--link-mode",
            self.link_mode.get(),
            "--stage-dim-candidates",
            "--stage-dim-candidates-limit",
            str(stage_limit),
        ]
        self.log_line("[run] " + " ".join(f'"{x}"' if " " in x else x for x in cmd))
        threading.Thread(target=self._run_builder_thread, args=(cmd, path), daemon=True).start()

    def _run_builder_thread(self, cmd: list[str], path: Path):
        try:
            proc = subprocess.Popen(cmd, cwd=str(SCRIPT_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            assert proc.stdout
            for line in proc.stdout:
                self.root.after(0, self.log_line, line)
            code = proc.wait()
            self.root.after(0, self.log_line, f"[exit] builder returned {code}")
            self.root.after(0, self.scan_root, path)
        except Exception as exc:
            self.root.after(0, self.log_line, f"[ERR] builder failed: {exc}")

    def refresh_sets(self):
        self.set_tree.delete(*self.set_tree.get_children())
        for idx, s in enumerate(self.spine_sets):
            skeleton = s.skeletons[0].name if s.skeletons else ""
            atlas = s.atlas.name if s.atlas else ""
            iid = str(idx)
            self.set_tree.insert("", tk.END, iid=iid, values=(len(s.pages), skeleton, atlas))

    def select_set(self, idx: int):
        if not self.spine_sets:
            return
        idx = max(0, min(idx, len(self.spine_sets) - 1))
        self.current_set_index = idx
        self.set_tree.selection_set(str(idx))
        self.set_tree.focus(str(idx))
        self.refresh_pages()
        self.select_page(0)

    def on_set_select(self, _event=None):
        sel = self.set_tree.selection()
        if not sel:
            return
        self.current_set_index = int(sel[0])
        self.refresh_pages()
        self.select_page(0)

    def refresh_pages(self):
        self.page_tree.delete(*self.page_tree.get_children())
        if not self.spine_sets:
            return
        s = self.spine_sets[self.current_set_index]
        for idx, p in enumerate(s.pages):
            key = set_key(s, p)
            cached = ""
            if key in self.state.get("correct", {}):
                cached = f"OK {self.state['correct'][key].get('rank')}"
            elif key in self.state.get("skipped", {}):
                cached = "skip"
            visible_count = len(self.visible_candidates((s, p))) if self.hide_blacklisted.get() else len(p.candidates)
            count = visible_count if visible_count == len(p.candidates) else f"{visible_count}/{len(p.candidates)}"
            self.page_tree.insert("", tk.END, iid=str(idx), values=(f"{p.page_index}: {p.atlas_page}", p.requested_size, count, cached))

    def select_page(self, idx: int):
        if not self.spine_sets:
            return
        pages = self.spine_sets[self.current_set_index].pages
        if not pages:
            return
        idx = max(0, min(idx, len(pages) - 1))
        self.current_page_index = idx
        self.page_tree.selection_set(str(idx))
        self.page_tree.focus(str(idx))
        self.refresh_candidates()
        self.select_candidate_by_index(0)

    def on_page_select(self, _event=None):
        sel = self.page_tree.selection()
        if not sel:
            return
        self.current_page_index = int(sel[0])
        self.refresh_candidates()
        self.select_candidate_by_index(0)

    def current_set_page(self) -> tuple[SpineSet, PageCandidates] | None:
        if not self.spine_sets:
            return None
        s = self.spine_sets[self.current_set_index]
        if not s.pages:
            return None
        return s, s.pages[self.current_page_index]

    def candidate_marked_correct(self, candidate: Candidate) -> bool:
        aliases = candidate_aliases(candidate)
        for entry in self.state.get("correct", {}).values():
            if aliases & state_entry_identities(entry):
                return True
        return False

    def candidate_used_elsewhere(self, key: str, candidate: Candidate) -> bool:
        aliases = candidate_aliases(candidate)
        for correct_key, entry in self.state.get("correct", {}).items():
            if correct_key != key and aliases & state_entry_identities(entry):
                return True
        for entry in self.state.get("used_global", {}).values():
            owner = str(entry.get("set_key") or "")
            if owner != key and aliases & state_entry_identities(entry):
                return True
        return False

    def is_candidate_blacklisted(self, s: SpineSet, p: PageCandidates, c: Candidate) -> bool:
        key = set_key(s, p)
        page_black = self.state.get("blacklist", {}).get(key, {})
        if str(c.rank) in page_black:
            return True
        aliases = candidate_aliases(c)
        if any(alias in self.state.get("blacklist_global", {}) for alias in aliases):
            return True
        return self.candidate_used_elsewhere(key, c)

    def visible_candidates(self, pair: tuple[SpineSet, PageCandidates]) -> list[Candidate]:
        s, p = pair
        if not self.hide_blacklisted.get():
            return list(p.candidates)
        return [c for c in p.candidates if not self.is_candidate_blacklisted(s, p, c)]

    def refresh_candidates(self):
        self.cand_tree.delete(*self.cand_tree.get_children())
        pair = self.current_set_page()
        if not pair:
            return
        s, p = pair
        visible_ranks = set()
        hidden_count = 0
        for c in p.candidates:
            blacklisted = self.is_candidate_blacklisted(s, p, c)
            if blacklisted and self.hide_blacklisted.get():
                hidden_count += 1
                continue
            tags = ("blacklisted",) if blacklisted else ()
            self.cand_tree.insert("", tk.END, iid=str(c.rank), values=(c.rank, c.score if c.score is not None else "", c.file_path.name, c.source_path, c.note), tags=tags)
            visible_ranks.add(c.rank)
        self.cand_tree.tag_configure("blacklisted", foreground="#888888")
        if self.current_candidate_rank not in visible_ranks:
            self.current_candidate_rank = None
        if hidden_count:
            self.status.set(f"[filter] hid {hidden_count} blacklisted candidate(s) on page {p.page_index}")
        self.update_preview()

    def select_candidate_by_index(self, idx: int):
        pair = self.current_set_page()
        if not pair:
            return
        candidates = self.visible_candidates(pair)
        if not candidates:
            self.current_candidate_rank = None
            self.update_preview()
            return
        idx = max(0, min(idx, len(candidates) - 1))
        rank = candidates[idx].rank
        self.cand_tree.selection_set(str(rank))
        self.cand_tree.focus(str(rank))
        self.cand_tree.see(str(rank))
        self.current_candidate_rank = rank
        self.update_preview()

    def on_candidate_select(self, _event=None):
        sel = self.cand_tree.selection()
        if sel:
            self.current_candidate_rank = int(sel[0])
            self.update_preview()

    def selected_candidate(self) -> Candidate | None:
        pair = self.current_set_page()
        if not pair or self.current_candidate_rank is None:
            return None
        for c in pair[1].candidates:
            if c.rank == self.current_candidate_rank:
                return c
        return None

    def update_thumbnail(self):
        if not hasattr(self, "thumbnail_canvas"):
            return
        canvas = self.thumbnail_canvas
        canvas.delete("all")
        c = self.selected_candidate()
        if not c:
            self.thumbnail_image = None
            self.thumbnail_path = None
            canvas.create_text(
                max(canvas.winfo_width() // 2, 120),
                max(canvas.winfo_height() // 2, 80),
                text="No candidate selected",
                fill="#d0d0d0",
            )
            return

        path = c.file_path
        max_w = max(canvas.winfo_width() - 16, 80)
        max_h = max(canvas.winfo_height() - 16, 80)
        try:
            self.thumbnail_image = self.load_thumbnail(path, max_w, max_h)
            self.thumbnail_path = path
            if self.thumbnail_image is None:
                raise RuntimeError("unsupported image format")
            img_w = self.thumbnail_image.width()
            img_h = self.thumbnail_image.height()
            canvas.create_image(max_w // 2 + 8, max_h // 2 + 8, image=self.thumbnail_image, anchor=tk.CENTER)
            canvas.create_text(8, 8, text=f"{path.name}  {img_w}x{img_h} preview", anchor=tk.NW, fill="#f0f0f0")
        except Exception as exc:
            self.thumbnail_image = None
            self.thumbnail_path = None
            canvas.create_text(
                max(canvas.winfo_width() // 2, 120),
                max(canvas.winfo_height() // 2, 80),
                text=f"Preview unavailable\n{path.name}\n{exc}",
                fill="#d0d0d0",
                justify=tk.CENTER,
            )

    def load_thumbnail(self, path: Path, max_w: int, max_h: int):
        try:
            from PIL import Image, ImageTk  # type: ignore

            with Image.open(path) as im:
                im.thumbnail((max_w, max_h))
                if im.mode not in ("RGB", "RGBA"):
                    im = im.convert("RGBA")
                return ImageTk.PhotoImage(im.copy())
        except ImportError:
            pass

        if path.suffix.lower() != ".png":
            return None
        img = tk.PhotoImage(file=str(path))
        factor = max(1, math.ceil(img.width() / max_w), math.ceil(img.height() / max_h))
        if factor > 1:
            img = img.subsample(factor, factor)
        return img

    def update_preview(self):
        self.preview.delete("1.0", tk.END)
        pair = self.current_set_page()
        if not pair:
            self.update_thumbnail()
            return
        s, p = pair
        c = self.selected_candidate()
        atlas_pages = parse_atlas_pages(s.atlas) if s.atlas else []
        lines = [
            f"set: {s.folder}",
            f"atlas: {s.atlas.name if s.atlas else '[missing]'}",
            f"skeletons: {', '.join(x.name for x in s.skeletons) if s.skeletons else '[missing]'}",
            f"page: {p.page_index} / {p.atlas_page}",
            f"requested_size: {p.requested_size}",
        ]
        if atlas_pages:
            lines.append("atlas_pages: " + ", ".join(f"{x['name']} {x['size']}".strip() for x in atlas_pages))
        if c:
            w, h = image_size(c.file_path)
            lines += [
                "",
                f"candidate_rank: {c.rank}",
                f"score: {c.score}",
                f"candidate_file: {c.file_path}",
                f"candidate_size: {w}x{h}" if w and h else "candidate_size: unknown",
                f"source: {c.source_path}",
                f"note: {c.note}",
            ]
        self.preview.insert("1.0", "\n".join(lines))
        self.update_thumbnail()

    def activate_rank(self, rank: int):
        pair = self.current_set_page()
        if not pair:
            return
        if any(c.rank == rank for c in pair[1].candidates):
            self.current_candidate_rank = rank
            self.cand_tree.selection_set(str(rank))
            self.cand_tree.focus(str(rank))
            self.cand_tree.see(str(rank))
            self.materialize_selected()

    def materialize_selected(self):
        pair = self.current_set_page()
        c = self.selected_candidate()
        if not pair or not c:
            return
        s, p = pair
        if not self.materializer.exists():
            messagebox.showerror("Missing materializer", str(self.materializer))
            return
        cmd = [
            sys.executable,
            str(self.materializer),
            "--materialize-built-set",
            str(s.folder),
            "--materialize-page",
            str(p.page_index),
            "--materialize-candidate",
            str(c.rank),
            "--link-mode",
            self.link_mode.get(),
        ]
        self.log_line(f"[activate] page {p.page_index} rank {c.rank}: {c.file_path.name}")
        threading.Thread(target=self._run_materialize_thread, args=(cmd,), daemon=True).start()

    def _run_materialize_thread(self, cmd: list[str]):
        try:
            proc = subprocess.run(cmd, cwd=str(SCRIPT_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace")
            out = (proc.stdout or "") + (proc.stderr or "")
            for line in out.splitlines():
                self.root.after(0, self.log_line, line)
            self.root.after(0, self.log_line, f"[exit] materializer returned {proc.returncode}")
            if proc.returncode == 0 and self.auto_viewer.get():
                self.root.after(0, self.launch_viewer)
        except Exception as exc:
            self.root.after(0, self.log_line, f"[ERR] materialize failed: {exc}")

    def launch_viewer(self):
        pair = self.current_set_page()
        if not pair:
            return
        s, _p = pair
        if not self.viewer.exists():
            messagebox.showerror("Missing viewer", str(self.viewer))
            return
        if self.restart_viewer.get() and self.viewer_proc and self.viewer_proc.poll() is None:
            try:
                self.viewer_proc.terminate()
            except OSError:
                pass
        args = [str(self.viewer)]
        if s.skeletons:
            args.append(str(s.skeletons[0]))
        elif s.atlas:
            args.append(str(s.atlas))
        try:
            self.viewer_proc = subprocess.Popen(args, cwd=str(s.folder))
            self.log_line(f"[viewer] launched: {' '.join(args)}")
        except Exception as exc:
            self.log_line(f"[ERR] viewer launch failed: {exc}")

    def previous_candidate(self):
        pair = self.current_set_page()
        if not pair:
            return
        ranks = [c.rank for c in self.visible_candidates(pair)]
        if not ranks:
            return
        rank = self.current_candidate_rank or ranks[0]
        idx = max(0, ranks.index(rank) - 1) if rank in ranks else 0
        self.select_candidate_by_index(idx)

    def next_candidate(self):
        pair = self.current_set_page()
        if not pair:
            return
        ranks = [c.rank for c in self.visible_candidates(pair)]
        if not ranks:
            return
        rank = self.current_candidate_rank or ranks[0]
        idx = min(len(ranks) - 1, ranks.index(rank) + 1) if rank in ranks else 0
        self.select_candidate_by_index(idx)

    def previous_page(self):
        self.select_page(self.current_page_index - 1)

    def next_page(self):
        self.select_page(self.current_page_index + 1)

    def mark_correct(self):
        pair = self.current_set_page()
        c = self.selected_candidate()
        if not pair or not c:
            return
        s, p = pair
        key = set_key(s, p)
        aliases = sorted(candidate_aliases(c))
        self.state.setdefault("correct", {})[key] = {
            "rank": c.rank,
            "score": c.score,
            "candidate_file": str(c.file_path),
            "source_path": c.source_path,
            "aliases": aliases,
        }
        self.state.get("blacklist", {}).get(key, {}).pop(str(c.rank), None)
        for alias in aliases:
            self.state.get("blacklist_global", {}).pop(alias, None)
        self.remember_used_candidate(key, p, c, "marked correct")
        save_state(self.state)
        self.refresh_pages()
        self.refresh_candidates()
        self.log_line(f"[OK] marked correct: page {p.page_index} rank {c.rank}")

    def blacklist_candidate(self):
        pair = self.current_set_page()
        c = self.selected_candidate()
        if not pair or not c:
            return
        s, p = pair
        reason = simpledialog.askstring("Blacklist", "Reason (optional):", parent=self.root) or ""
        key = set_key(s, p)
        self.state.setdefault("blacklist", {}).setdefault(key, {})[str(c.rank)] = {
            "candidate_file": str(c.file_path),
            "source_path": c.source_path,
            "aliases": sorted(candidate_aliases(c)),
            "reason": reason,
        }
        aliases = sorted(candidate_aliases(c))
        global_note = "local only; already marked correct elsewhere"
        if not self.candidate_marked_correct(c):
            entry = {
                "candidate_file": str(c.file_path),
                "source_path": c.source_path,
                "aliases": aliases,
                "reason": reason,
                "first_seen": key,
            }
            for alias in aliases:
                self.state.setdefault("blacklist_global", {})[alias] = entry
            global_note = "global"
        save_state(self.state)
        self.refresh_pages()
        self.refresh_candidates()
        self.select_candidate_by_index(0)
        self.log_line(f"[MISS] blacklisted: page {p.page_index} rank {c.rank} ({global_note})")

    def skip_page(self):
        pair = self.current_set_page()
        if not pair:
            return
        s, p = pair
        self.state.setdefault("skipped", {})[set_key(s, p)] = {"folder": str(s.folder)}
        save_state(self.state)
        self.refresh_pages()
        self.next_page()
        self.log_line(f"[skip] page {p.page_index}: {p.atlas_page}")

    def current_materialized_rank(self, s: SpineSet, page_index: int) -> int | None:
        note = s.folder / "_materialized_history" / f"page{page_index:02d}_current.txt"
        text = read_text(note)
        for line in text.splitlines():
            if line.startswith("candidate_rank:"):
                try:
                    return int(line.split(":", 1)[1].strip())
                except ValueError:
                    return None
        return None

    def find_candidate_by_rank(self, page: PageCandidates, rank: int) -> Candidate | None:
        for candidate in page.candidates:
            if candidate.rank == rank:
                return candidate
        return None

    def finalize_choices(self, s: SpineSet) -> tuple[list[tuple[PageCandidates, Candidate, str]], list[PageCandidates]]:
        choices = []
        missing = []
        correct_state = self.state.get("correct", {})
        for page in s.pages:
            key = set_key(s, page)
            rank = None
            source = ""
            if key in correct_state:
                try:
                    rank = int(correct_state[key].get("rank"))
                    source = "marked correct"
                except (TypeError, ValueError):
                    rank = None
            if rank is None:
                rank = self.current_materialized_rank(s, page.page_index)
                source = "activated"
            candidate = self.find_candidate_by_rank(page, rank) if rank is not None else None
            if candidate:
                choices.append((page, candidate, source))
            elif key not in self.state.get("skipped", {}):
                missing.append(page)
        return choices, missing

    def remember_used_candidate(self, key: str, page: PageCandidates, candidate: Candidate, source: str) -> None:
        aliases = sorted(candidate_aliases(candidate))
        entry = {
            "set_key": key,
            "page_index": page.page_index,
            "atlas_page": page.atlas_page,
            "rank": candidate.rank,
            "score": candidate.score,
            "candidate_file": str(candidate.file_path),
            "source_path": candidate.source_path,
            "aliases": aliases,
            "source": source,
        }
        used = self.state.setdefault("used_global", {})
        for alias in aliases:
            used[alias] = entry

    def normalized_finalize_mode(self) -> str:
        mode = self.finalize_mode.get()
        if mode not in FINALIZE_MODES:
            mode = "move"
            self.finalize_mode.set(mode)
        self.save_settings()
        return mode

    def confirm_symlink_finalize_if_needed(self, mode: str) -> bool:
        if mode == "symlink" and self.delete_candidates_after_finalize.get():
            return messagebox.askyesno(
                "Finalize with symlinks?",
                "Symlink finalize can still depend on source images outside this folder. Continue with symlink mode?",
                parent=self.root,
            )
        return True

    def current_scan_path(self, fallback: Path) -> Path:
        raw = self.path_var.get().strip().strip('"')
        if raw:
            path = Path(raw).expanduser()
            if path.exists():
                return path.parent if path.is_file() else path
        return fallback

    def finalize_current_set(self):
        if not self.spine_sets:
            return
        s = self.spine_sets[self.current_set_index]
        mode = self.normalized_finalize_mode()

        choices, missing = self.finalize_choices(s)
        if not choices:
            messagebox.showerror("Nothing to finalize", "No marked-correct or activated candidate choices were found for this set.")
            return
        if missing:
            sample = "\n".join(f"page {p.page_index}: {p.atlas_page}" for p in missing[:8])
            if len(missing) > 8:
                sample += f"\n...and {len(missing) - 8} more"
            if not messagebox.askyesno(
                "Finalize incomplete set?",
                f"{len(missing)} page(s) have no selected candidate:\n\n{sample}\n\nFinalize the selected pages anyway?",
                parent=self.root,
            ):
                return
        if not self.confirm_symlink_finalize_if_needed(mode):
            return

        threading.Thread(
            target=self._run_finalize_many_thread,
            args=([(s, choices)], mode, bool(self.delete_candidates_after_finalize.get()), self.current_scan_path(s.folder)),
            daemon=True,
        ).start()

    def finalize_all_sets(self):
        if not self.spine_sets:
            return
        mode = self.normalized_finalize_mode()
        plans: list[tuple[SpineSet, list[tuple[PageCandidates, Candidate, str]]]] = []
        no_choices = 0
        missing_pages: list[tuple[SpineSet, PageCandidates]] = []
        total_pages = 0
        for s in self.spine_sets:
            choices, missing = self.finalize_choices(s)
            if not choices:
                no_choices += 1
                continue
            plans.append((s, choices))
            total_pages += len(choices)
            missing_pages.extend((s, p) for p in missing)

        if not plans:
            messagebox.showerror("Nothing to finalize", "No marked-correct or activated candidate choices were found in the loaded tree.")
            return

        sample = "\n".join(f"{s.folder.name}: page {p.page_index} {p.atlas_page}" for s, p in missing_pages[:8])
        if len(missing_pages) > 8:
            sample += f"\n...and {len(missing_pages) - 8} more"
        details = [
            f"Finalize {len(plans)} set(s) and {total_pages} selected page(s)?",
            "",
            f"Finalize mode: {mode}",
            f"Delete _candidates: {bool(self.delete_candidates_after_finalize.get())}",
        ]
        if missing_pages:
            details.extend(["", f"{len(missing_pages)} page(s) in these sets have no selected candidate:", sample])
        if no_choices:
            details.extend(["", f"{no_choices} set(s) with no choices will be left untouched."])
        details.extend(["", "Continue?"])
        if not messagebox.askyesno("Finalize all loaded sets?", "\n".join(details), parent=self.root):
            return
        if not self.confirm_symlink_finalize_if_needed(mode):
            return

        scan_path = self.current_scan_path(self.spine_sets[0].folder)
        threading.Thread(
            target=self._run_finalize_many_thread,
            args=(plans, mode, bool(self.delete_candidates_after_finalize.get()), scan_path),
            daemon=True,
        ).start()

    def _run_finalize_one_set(self, s: SpineSet, choices: list[tuple[PageCandidates, Candidate, str]], mode: str, delete_candidates: bool) -> int:
        if not s.atlas:
            raise RuntimeError(f"No atlas found in {s.folder}")
        atlas_pages = parse_atlas_pages(s.atlas)
        if not atlas_pages:
            raise RuntimeError(f"Could not parse atlas pages from {s.atlas}")

        finalized = 0
        remaining_by_identity: dict[str, int] = {}
        for _page, candidate, _source in choices:
            identity = candidate_identity(candidate)
            remaining_by_identity[identity] = remaining_by_identity.get(identity, 0) + 1
        self.root.after(0, self.log_line, f"[finalize] {s.folder}")
        for page, candidate, source in choices:
            if page.page_index < 1 or page.page_index > len(atlas_pages):
                raise IndexError(f"Page {page.page_index} is out of range for {s.atlas.name}")
            target_name = Path(atlas_pages[page.page_index - 1]["name"]).name
            if not target_name:
                raise RuntimeError(f"Atlas page {page.page_index} has no usable filename")
            target = s.folder / target_name
            key = set_key(s, page)
            identity = candidate_identity(candidate)
            place_mode = "copy" if mode == "move" and remaining_by_identity.get(identity, 0) > 1 else mode
            placed = place_final_texture(candidate.file_path, target, place_mode)
            remaining_by_identity[identity] = remaining_by_identity.get(identity, 1) - 1
            finalized += 1
            self.remember_used_candidate(key, page, candidate, f"finalized via {source}")
            self.root.after(
                0,
                self.log_line,
                f"[OK] finalized page {page.page_index} rank {candidate.rank} -> {target.name} via {placed} ({source})",
            )

        if delete_candidates:
            cand_root = s.folder / "_candidates"
            if cand_root.exists():
                shutil.rmtree(cand_root)
                self.root.after(0, self.log_line, f"[OK] deleted {cand_root}")
        return finalized

    def _run_finalize_many_thread(self, plans: list[tuple[SpineSet, list[tuple[PageCandidates, Candidate, str]]]], mode: str, delete_candidates: bool, scan_path: Path):
        try:
            total = 0
            for s, choices in plans:
                total += self._run_finalize_one_set(s, choices, mode, delete_candidates)
            save_state(self.state)
            self.root.after(0, self.log_line, f"[OK] finalize complete: {total} page(s) across {len(plans)} set(s)")
            self.root.after(0, self.scan_root, scan_path)
        except Exception as exc:
            self.root.after(0, self.log_line, f"[ERR] finalize failed: {exc}")

    def open_current_folder(self):
        pair = self.current_set_page()
        if not pair:
            return
        os.startfile(str(pair[0].folder))


def scan_summary(path: Path) -> int:
    sets = discover_spine_sets(path)
    print(json.dumps(
        {
            "root": str(path),
            "sets": [
                {
                    "folder": str(s.folder),
                    "atlas": str(s.atlas) if s.atlas else None,
                    "skeletons": [str(x) for x in s.skeletons],
                    "pages": [
                        {
                            "page_index": p.page_index,
                            "atlas_page": p.atlas_page,
                            "requested_size": p.requested_size,
                            "candidate_count": len(p.candidates),
                            "top_rank": p.candidates[0].rank if p.candidates else None,
                            "top_score": p.candidates[0].score if p.candidates else None,
                        }
                        for p in s.pages
                    ],
                }
                for s in sets
            ],
        },
        indent=2,
    ))
    return 0 if sets else 1


def main():
    ap = argparse.ArgumentParser(description="GUI candidate picker for Spine Magic Builder staged candidates.")
    ap.add_argument("path", nargs="?", help="Built set folder or scan root.")
    ap.add_argument("--scan-only", action="store_true", help="Print discovered sets/pages as JSON and exit.")
    args = ap.parse_args()
    start_path = Path(args.path).resolve() if args.path else None
    if args.scan_only:
        if not start_path:
            ap.error("--scan-only requires a path")
        raise SystemExit(scan_summary(start_path))

    root = tk.Tk()
    app = CandidatePickerApp(root, start_path)
    root.mainloop()


if __name__ == "__main__":
    main()
