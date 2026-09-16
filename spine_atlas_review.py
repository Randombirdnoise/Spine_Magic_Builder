"""On-demand atlas candidate review; no candidate caps or source mutations."""
import json
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


def index_report(path):
    """Keep offsets and small summaries in memory, not the entire pair matrix."""
    records = []
    with Path(path).open('rb') as stream:
        header = json.loads(stream.readline())
        if header.get('type') != 'header' or 'source_root' not in header:
            raise ValueError('This is not a current atlas_match_report.jsonl file')
        while True:
            offset = stream.tell()
            line = stream.readline()
            if not line:
                break
            row = json.loads(line)
            if row.get('type') == 'skeleton':
                records.append((offset, row['skeleton'], row['status'], row['selected_candidates']))
    return header, records


def read_record(path, offset):
    with Path(path).open('rb') as stream:
        stream.seek(offset)
        return json.loads(stream.readline())


class AtlasReviewWindow:
    def __init__(self, parent, report_path, viewer_path):
        self.window = tk.Toplevel(parent)
        self.window.title('Spine Magic Builder - Atlas Matches')
        self.window.geometry('1250x760')
        self.report_path = Path(report_path)
        self.viewer_path = viewer_path
        self.events = queue.Queue()
        self.records = []
        self.header = None
        self.row = None
        self.generation = 0
        self.busy = False
        self.closed = False
        self.texture_cache = {}
        self.output = None
        self.status = tk.StringVar(value='Indexing matching report...')
        ttk.Label(self.window, text=str(report_path)).pack(fill=tk.X, padx=8, pady=5)
        ttk.Label(self.window, text='All evaluated atlases are listed, including weak and zero-hit candidates. A top rank is provisional.').pack(fill=tk.X, padx=8)
        pane = ttk.Panedwindow(self.window, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        left, right = ttk.Frame(pane), ttk.Frame(pane)
        pane.add(left, weight=1)
        pane.add(right, weight=2)
        self.skeletons = self.tree(left, ('skeleton', 'status', 'candidates'), (270, 110, 70))
        self.atlases = self.tree(right, ('atlas', 'exact', 'weak', 'missing', 'score'), (290, 55, 55, 65, 80))
        self.skeletons.bind('<<TreeviewSelect>>', self.select_skeleton)
        self.atlases.bind('<<TreeviewSelect>>', self.select_atlas)
        self.details = tk.Text(self.window, height=8, wrap='word', state='disabled')
        self.details.pack(fill=tk.X, padx=8)
        controls = ttk.Frame(self.window)
        controls.pack(fill=tk.X, padx=8, pady=8)
        self.build_button = ttk.Button(controls, text='Build selected atlas pair', command=self.materialize, state='disabled')
        self.build_button.pack(side=tk.LEFT)
        self.view_button = ttk.Button(controls, text='Open built pair in SpineViewer', command=self.open_viewer, state='disabled')
        self.view_button.pack(side=tk.LEFT, padx=8)
        ttk.Label(self.window, textvariable=self.status, wraplength=1220).pack(fill=tk.X, padx=8, pady=8)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.worker('index', lambda: index_report(self.report_path))
        self.window.after(80, self.poll)

    @staticmethod
    def tree(parent, columns, widths):
        tree = ttk.Treeview(parent, columns=columns, show='headings', selectmode='browse')
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(fill=tk.BOTH, expand=True)
        for column, width in zip(columns, widths):
            tree.heading(column, text=column.title())
            tree.column(column, width=width, minwidth=45)
        return tree

    def worker(self, kind, callback, generation=None):
        def run():
            try:
                self.events.put((kind, generation, callback(), None))
            except Exception as exc:
                self.events.put((kind, generation, None, str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                kind, generation, result, error = self.events.get_nowait()
                if kind == 'row' and generation != self.generation:
                    continue
                if kind == 'build':
                    self.busy = False
                    self.skeletons.configure(selectmode='browse')
                    self.atlases.configure(selectmode='browse')
                if error:
                    self.status.set(error)
                    messagebox.showerror('Atlas review', error, parent=self.window)
                    self.select_atlas()
                elif kind == 'index':
                    self.header, self.records = result
                    for i, (_, source, status, count) in enumerate(self.records):
                        self.skeletons.insert('', 'end', iid=str(i), values=(Path(source).name, status, count))
                    self.status.set(f'{len(self.records)} skeletons. Select one to inspect every atlas candidate.')
                elif kind == 'row':
                    self.row = result
                    for i, candidate in enumerate(self.row['candidates']):
                        self.atlases.insert('', 'end', iid=str(i), values=(Path(candidate['atlas']).name,
                            candidate['region_hits'], candidate['weak_region_hits'], len(candidate['missing_regions']),
                            candidate['weighted_region_score']))
                    self.status.set(f"{len(self.row['candidates'])} atlases evaluated for {self.row['skeleton']}")
                elif kind == 'build':
                    self.output = Path(result)
                    self.view_button.configure(state='normal')
                    self.status.set(f'Built: {result}')
                    self.select_atlas()
        except queue.Empty:
            pass
        self.window.after(80, self.poll)

    def select_skeleton(self, _event=None):
        if self.busy:
            return
        selected = self.skeletons.selection()
        if not selected:
            return
        self.generation += 1
        self.row = None
        self.atlases.delete(*self.atlases.get_children())
        self.build_button.configure(state='disabled')
        self.view_button.configure(state='disabled')
        self.output = None
        offset = self.records[int(selected[0])][0]
        self.status.set('Loading candidates...')
        self.worker('row', lambda: read_record(self.report_path, offset), self.generation)

    def select_atlas(self, _event=None):
        if _event is not None:
            self.output = None
            self.view_button.configure(state='disabled')
        selected = self.atlases.selection()
        enabled = bool(self.row and selected and not self.busy)
        self.build_button.configure(state='normal' if enabled else 'disabled')
        if not enabled:
            return
        candidate = self.row['candidates'][int(selected[0])]
        self.details.configure(state='normal')
        self.details.delete('1.0', tk.END)
        self.details.insert('1.0', json.dumps(candidate, ensure_ascii=False, indent=2))
        self.details.configure(state='disabled')

    def materialize(self):
        selected = self.atlases.selection()
        if self.busy or not self.row or not selected:
            return
        self.busy = True
        self.skeletons.configure(selectmode='none')
        self.atlases.configure(selectmode='none')
        self.build_button.configure(state='disabled')
        self.view_button.configure(state='disabled')
        self.status.set('Building pair; indexing textures if needed...')
        row = self.row
        atlas_path = row['candidates'][int(selected[0])]['atlas']
        def build():
            import spine_magic_builder_candidate_materializer_v3 as api
            from spine_atlas_matching import materialize_selection
            return materialize_selection(api, self.report_path, self.header, row, atlas_path, self.texture_cache)
        self.worker('build', build)

    def open_viewer(self):
        if not self.output:
            return
        viewer = Path(self.viewer_path)
        if not viewer.is_file():
            chosen = filedialog.askopenfilename(parent=self.window, title='Select SpineViewer.exe', filetypes=[('Executable', '*.exe')])
            if not chosen:
                return
            viewer = Path(chosen)
            self.viewer_path = str(viewer)
        try:
            skeletons = sorted([*self.output.glob('*.skel'), *self.output.glob('*.json')])
            if not skeletons:
                raise OSError('Built skeleton was not found')
            subprocess.Popen([str(viewer), str(skeletons[0])], cwd=str(self.output))
        except OSError as exc:
            messagebox.showerror('SpineViewer', str(exc), parent=self.window)

    def close(self):
        if self.busy:
            self.status.set('Wait for the current pair build to finish before closing this window.')
            return
        self.closed = True
        self.window.destroy()


def open_atlas_review(parent, initial_path, viewer_path):
    root = Path(initial_path)
    candidate = root / 'atlas_match_report.jsonl'
    if not candidate.is_file():
        chosen = filedialog.askopenfilename(parent=parent, initialdir=str(root) if root.is_dir() else None,
                                           title='Open atlas matching report', filetypes=[('Matching reports', '*.jsonl')])
        if not chosen:
            return
        candidate = Path(chosen)
    return AtlasReviewWindow(parent, candidate, viewer_path)
