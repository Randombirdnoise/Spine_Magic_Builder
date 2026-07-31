#!/usr/bin/env python3
import argparse, re, shutil, struct, json, sys, hashlib, os
from pathlib import Path
from typing import Iterator, Optional

# -------------------- skeleton JSON trimmer (helpers) --------------------
_JSON_START_MARKS = [
    '{"skeleton":{"hash":',
    ':{"skeleton":{"hash":',
    '"skeleton":{"hash":',
    '{"skeleton":{',
    '"skeleton":{'
]

def _find_json_start(txt: str):
    best = None
    for mark in _JSON_START_MARKS:
        i = txt.find(mark)
        if i != -1:
            start = i if mark.startswith('{') else txt.rfind('{', 0, i + 1)
            if start is not None and start >= 0:
                best = start if (best is None or start < best) else best
    return best

def _extract_balanced_json(txt: str, start: int):
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(txt[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return txt[start:i+1]
    return None

def try_extract_skeleton_json_text(txt: str):
    start = _find_json_start(txt)
    if start is None:
        return None
    candidate = _extract_balanced_json(txt, start)
    if not candidate:
        return None
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict) and ("skeleton" in obj or "bones" in obj):
            return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), indent=None)
    except Exception:
        pass
    return None

MOVED_MAP = {}
FILE_HASH_CACHE = {}
OUTPUT_DEDUPE = {}

# -------------------- tiny utils --------------------
def read_bytes(p: Path) -> bytes:
    try: return p.read_bytes()
    except Exception: return b""

def read_text(p: Path, max_bytes: int | None = None) -> str:
    try:
        if max_bytes is not None:
            with p.open("rb") as f:
                return f.read(max_bytes).decode("utf-8", errors="replace")
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

def ensure_parent(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)

def _relpath(src: Path, dst_parent: Path) -> str:
    try:
        return os.path.relpath(str(src), start=str(dst_parent))
    except Exception:
        return str(src)

def link_or_copy(src: Path, dst: Path, link_mode: str) -> bool:
    """Place src at dst using link_mode: copy|symlink|hardlink.

    - symlink: creates a file symlink (relative when possible)
    - hardlink: creates a hard link (same volume only)
    - copy: shutil.copy2
    Falls back to copy if linking fails.
    """
    ensure_parent(dst)

    # If already exists, treat as success (avoid thrashing)
    if dst.exists():
        return True

    # If user asked for copy explicitly
    if link_mode == "copy":
        try:
            shutil.copy2(src, dst)
            return True
        except Exception as e:
            print(f"[WARN] copy failed {src} -> {dst}: {e}")
            return False

    # Try requested link type first
    def _try_symlink() -> bool:
        try:
            target = _relpath(src, dst.parent)
            os.symlink(target, dst)  # file link
            return True
        except Exception as e:
            # Windows common: [WinError 1314] privilege not held
            print(f"[WARN] symlink failed {src} -> {dst}: {e}")
            return False

    def _try_hardlink() -> bool:
        try:
            os.link(src, dst)
            return True
        except Exception as e:
            print(f"[WARN] hardlink failed {src} -> {dst}: {e}")
            return False

    ok = False
    if link_mode == "symlink":
        ok = _try_symlink()
        if not ok:
            # symlink often fails on Windows; try hardlink as a pragmatic fallback
            ok = _try_hardlink()
    elif link_mode == "hardlink":
        ok = _try_hardlink()
        if not ok:
            ok = _try_symlink()

    if ok:
        return True

    # Final fallback: copy
    try:
        shutil.copy2(src, dst)
        return True
    except Exception as e:
        print(f"[WARN] fallback copy failed {src} -> {dst}: {e}")
        return False

def is_probable_spine_entity_root(folder: Path) -> bool:
    """
    True if this folder looks like one self-contained Spine asset set.
    Search recursively under the folder, not just the top level.
    Conservative enough to avoid obvious false positives, but flexible enough
    for named folders that store images/atlas/json one level deeper.
    """
    if not folder.is_dir():
        return False

    has_atlas = False
    has_skel = False
    has_image = False

    try:
        for p in folder.rglob("*"):
            if not p.is_file():
                continue

            # Never count prior outputs
            lowered_parts = [part.lower() for part in p.parts]
            if any(part.startswith("spine_built") for part in lowered_parts):
                continue

            name = p.name.lower()
            suf = p.suffix.lower()

            if suf == ".atlas" or name.endswith(".atlas.txt"):
                has_atlas = True
            elif suf in (".skel", ".json"):
                has_skel = True
            elif suf in (".png", ".jpg", ".jpeg", ".webp"):
                has_image = True

            if has_atlas and has_skel and has_image:
                return True
    except Exception:
        return False

    return False


def find_entity_roots(root: Path) -> list[Path]:
    """
    Treat each immediate child directory of root as a possible independent Spine entity.
    Skip build output folders to avoid rescanning prior results.
    """
    out = []

    try:
        for child in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if not child.is_dir():
                continue

            # Never recurse into prior outputs
            if child.name.lower().startswith("spine_built"):
                continue

            if is_probable_spine_entity_root(child):
                out.append(child)
    except Exception:
        return []

    return out


def process_one_root(
    scan_root: Path,
    out_root: Path,
    args,
) -> tuple[int, int, int]:
    """
    Run the existing pipeline against one isolated root.
    Returns: (built, matched, total_skeletons)
    """
    print(f"\n[entity] {scan_root}")

    print(f"[info] scanning for atlases…")
    atlases = index_atlases_by_magic(scan_root)
    print(f"[info] atlases detected: {len(atlases)}")

    print(f"[info] indexing textures…")
    tex_lut = build_texture_lookup(scan_root)

    print(f"[info] scanning for skeletons…")
    skels = gather_skeletons_by_magic(scan_root)
    print(f"[info] skeleton candidates: {len(skels)}")

    built = 0
    matched = 0

    for sk in skels:
        ranked = rank_atlases(sk, atlases)
        best = choose_best_atlas(sk, atlases, args.min_hits, args.aggressive_atlas)

        if args.top_n > 0 and ranked:
            print(f"\n[top] {sk.path.name}")
            for (rh, ph, nb, prox), info in ranked[:args.top_n]:
                conf = confidence_label(rh, ph, prox)
                print(f"  - {info['path'].name}  region={rh} page={ph} name_bonus={nb} prox={prox}  conf={conf}")

        if args.explain_match and ranked:
            _print_ranked_atlas_debug(sk, ranked, max(args.top_n, 5))

        try:
            export_set(
                sk, best, tex_lut, out_root,
                move=args.move,
                link_mode=args.link_mode,
                allow_reuse_textures=args.allow_reuse_textures,
                dims_fallback=args.dims_fallback,
                prefer_nearby_textures=args.prefer_nearby_textures,
                prefer_consistent_texture_dir=args.prefer_consistent_texture_dir,
                rewrite_pages_to_match_source=args.rewrite_pages_to_match_source,
                explain_match=args.explain_match,
                dedupe_textures=args.dedupe_textures,
                stage_dim_candidates=args.stage_dim_candidates,
                stage_dim_candidates_limit=args.stage_dim_candidates_limit,
            )
            built += 1

            if best or (sk.embedded_atlas_text and sk.embedded_atlas_pages):
                matched += 1
                if best:
                    rh, ph, _nb, prox = score_atlas_for_skeleton(sk, best)
                    conf = confidence_label(rh, ph, prox)
                    print(f"[OK] {sk.path.name} -> {best['path'].name} (region={rh}, page={ph}, prox={prox}, conf={conf})")
                else:
                    print(f"[OK] {sk.path.name} -> (embedded atlas)")
            else:
                print(f"[OK] {sk.path.name} -> (no atlas; skeleton normalized)")
        except Exception as e:
            print(f"[WARN] failed set for {sk.path.name}: {e}")

    print(f"[done/entity] built: {built}/{len(skels)}   atlas matched/embedded: {matched}/{len(skels)}")
    return built, matched, len(skels)



def _canon(p: Path) -> str:
    try: return str(p.resolve())
    except Exception: return str(p.absolute())

def stable_short_id(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()[:8]

def sha1_file(path: Path) -> str | None:
    key = _canon(path)
    if key in FILE_HASH_CACHE:
        return FILE_HASH_CACHE[key]
    h = hashlib.sha1()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                if not chunk:
                    break
                h.update(chunk)
        digest = h.hexdigest()
        FILE_HASH_CACHE[key] = digest
        return digest
    except Exception:
        return None

def place_deduped(existing_dst: Path, new_dst: Path, link_mode: str) -> bool:
    return link_or_copy(existing_dst, new_dst, ("hardlink" if link_mode == "copy" else link_mode))

def path_distance(a: Path, b: Path) -> int:
    try:
        ap = a.resolve()
        bp = b.resolve()
    except Exception:
        ap = a
        bp = b
    a_parts = ap.parts
    b_parts = bp.parts
    common = 0
    for x, y in zip(a_parts, b_parts):
        if x.lower() != y.lower():
            break
        common += 1
    return (len(a_parts) - common) + (len(b_parts) - common)

def unique_output_dir(out_root: Path, base_stem: str, sk_path: Path) -> Path:
    """
    Prevent overwrite: if base exists, suffix with a stable short hash derived from skeleton path.
    """
    candidate = out_root / base_stem
    if not candidate.exists():
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    tag = stable_short_id(_canon(sk_path))
    candidate = out_root / f"{base_stem}__{tag}"
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate

def copy_or_move(src: Path, dst: Path, move: bool, link_mode: str = "copy") -> bool:
    ensure_parent(dst)
    src_key = _canon(src)
    real_src = src
    if not real_src.exists():
        alt = MOVED_MAP.get(src_key)
        if alt and Path(alt).exists():
            real_src = Path(alt)
    if not real_src.exists():
        print(f"[WARN] missing source, skipping: {src}")
        return False

    if move:
        if dst.exists():
            return True
        try:
            real_src.replace(dst)
        except OSError:
            try:
                shutil.move(str(real_src), str(dst))
            except Exception as e:
                print(f"[WARN] move failed {real_src} -> {dst}: {e}")
                return False
        MOVED_MAP[src_key] = _canon(dst)
        return True
    else:
        try:
            if _canon(real_src) != _canon(dst):
                return link_or_copy(real_src, dst, link_mode)
            return True
        except Exception as e:
            print(f"[WARN] place failed {real_src} -> {dst}: {e}")
            return False

# -------------------- image dim readers --------------------
_PIL = None
try:
    from PIL import Image as _PIL_Image  # type: ignore
    _PIL = _PIL_Image
except Exception:
    _PIL = None

def _dims_png(buf: bytes):
    if not buf.startswith(b"\x89PNG\r\n\x1a\n"): return None
    if len(buf) < 24: return None
    return struct.unpack(">II", buf[16:24])

def _dims_jpeg_fast(f: Path):
    try:
        with f.open("rb") as fp:
            if fp.read(2) != b"\xFF\xD8": return None
            while True:
                b = fp.read(1)
                if not b: return None
                if b != b"\xFF": continue
                while True:
                    b = fp.read(1)
                    if not b: return None
                    if b != b"\xFF": break
                m = b[0]
                if m in (0xD8,0xD9,0x01): continue
                seg_len_b = fp.read(2)
                if len(seg_len_b) < 2: return None
                seg_len = struct.unpack(">H", seg_len_b)[0]
                if seg_len < 2: return None
                if 0xC0 <= m <= 0xC3 or 0xC5 <= m <= 0xC7 or 0xC9 <= m <= 0xCB or 0xCD <= m <= 0xCF:
                    data = fp.read(seg_len - 2)
                    if len(data) < 5: return None
                    h, w = struct.unpack(">HH", data[1:5])
                    return (w, h)
                fp.seek(seg_len - 2, 1)
    except Exception:
        return None

def get_image_dims(path: Path):
    ext = path.suffix.lower()
    if _PIL and ext in (".png",".jpg",".jpeg",".webp"):
        try:
            with _PIL.open(path) as im:
                return (int(im.width), int(im.height))
        except Exception:
            pass
    head = b""
    try:
        with path.open("rb") as f: head = f.read(64*1024)
    except Exception:
        return (None,None)
    if ext == ".png":
        v = _dims_png(head); return v if v else (None,None)
    if ext in (".jpg",".jpeg"):
        v = _dims_jpeg_fast(path); return v if v else (None,None)
    return (None,None)

# -------------------- Spine detectors --------------------
# Match a Spine-like version without requiring word boundaries
_SPINE_VER_RE = re.compile(rb"([234]\.\d{1,2}\.\d{1,2})")

# Heuristic: Spine binary often stores strings with a small length prefix byte
# before the ASCII. Version strings are usually 5–12 chars (e.g., "\x07 3.6.52 E...")
_SPINE_VER_LENPREFIX_RE = re.compile(rb"[\x05-\x0c]([234]\.\d{1,2}\.\d{1,2})")

def detect_spine_binary(buf: bytes) -> tuple[bool, str | None]:
    head = buf[:65536]

    # 1) If "spine" appears, the version is often nearby in the header
    i = head.find(b"spine")
    if i != -1:
        window = head[max(0, i - 64): i + 256]
        m = _SPINE_VER_RE.search(window)
        if m:
            return True, m.group(1).decode("ascii", "ignore")

    # 2) Otherwise use the length-prefix heuristic
    m = _SPINE_VER_LENPREFIX_RE.search(head)
    if m:
        return True, m.group(1).decode("ascii", "ignore")

    # 3) Final fallback
    m = _SPINE_VER_RE.search(head)
    if m:
        return True, m.group(1).decode("ascii", "ignore")

    return False, None

def detect_spine_json_text(txt: str) -> tuple[bool, str | None]:
    try:
        data = json.loads(txt)
        if not isinstance(data, dict): return (False, None)
        has_skel = ("skeleton" in data) or ("bones" in data)
        has_slots = ("slots" in data) or ("skins" in data)
        ver = None
        if isinstance(data.get("skeleton"), dict):
            v = data["skeleton"].get("spine")
            if isinstance(v, str): ver = v
        return (bool(has_skel and has_slots), ver)
    except Exception:
        return (False, None)

# -------------------- Cocos wrapper detector/extractor --------------------
def is_cocos_spine_wrapper_text(txt: str) -> bool:
    if not txt:
        return False
    if '"skeletonJsonStr"' not in txt:
        return False
    if '"__type__"' in txt and "sp.SkeletonData" in txt:
        return True
    if '"_atlasText"' in txt and '"textureNames"' in txt:
        return True
    return False

def try_parse_cocos_wrapper(txt: str) -> Optional[dict]:
    try:
        obj = json.loads(txt)
        if not isinstance(obj, dict):
            return None
        if not isinstance(obj.get("skeletonJsonStr"), str):
            return None
        if not isinstance(obj.get("_atlasText"), str):
            return None
        return obj
    except Exception:
        return None

# Spine atlas metadata lines may be indented (tabs/spaces), especially in 4.x exports.
_ATLAS_KEY_RE = re.compile(r"^\s*(size|format|filter|repeat|pma)\s*:", re.I | re.M)

def detect_spine_atlas_text(txt: str) -> bool:
    blocks = re.split(r"\r?\n\s*\r?\n", (txt or "").strip())
    for sec in blocks:
        if _ATLAS_KEY_RE.search(sec):
            return True
    return False

# -------------------- atlas parsing (pages + regions) --------------------
def parse_atlas_pages_regions(atlas_text: str):
    pages = []
    blocks = re.split(r"\r?\n\s*\r?\n", (atlas_text or "").strip())
    for sec in blocks:
        lines = sec.splitlines()
        i = 0
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            continue

        page_name = Path(lines[i].strip()).name
        W = H = None
        regions = []

        i += 1
        while i < len(lines):
            ln = lines[i].strip()
            if not ln:
                i += 1
                continue
            m = re.match(r"size\s*:\s*(\d+)\s*,\s*(\d+)", ln, re.I)
            if m:
                W, H = int(m.group(1)), int(m.group(2))
                i += 1
                continue
            if re.match(r"^(format|filter|repeat|pma)\s*:", ln, re.I):
                i += 1
                continue
            break

        while i < len(lines):
            name = lines[i].strip()
            if not name:
                i += 1
                continue
            if ":" in name:
                i += 1
                continue
            regions.append(name)
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    i += 1
                    continue
                if nxt.startswith(" ") or nxt.startswith("\t"):
                    i += 1
                    continue
                break

        pages.append({"name": page_name, "size": (W, H), "regions": regions})
    return pages

def index_atlases_by_magic(root: Path):
    infos = []
    for p in root.rglob("*"):
        if not p.is_file(): continue
        txt_head = read_text(p, max_bytes=256*1024)
        if not txt_head: continue
        if detect_spine_atlas_text(txt_head):
            txt_full = read_text(p)
            pages = parse_atlas_pages_regions(txt_full)
            if not pages: continue
            stem = p.name[:-10] if p.name.lower().endswith(".atlas.txt") else p.stem
            flat_regions = []
            for pg in pages:
                flat_regions.extend(pg.get("regions") or [])
            infos.append({"path": p, "pages": pages, "stem": stem, "text": txt_full, "regions": flat_regions})
    return infos

# -------------------- textures index --------------------
def build_texture_lookup(root: Path):
    by_name, by_stem, by_canonical_stem, by_dims = {}, {}, {}, {}
    def add(d, k, v):
        if k:
            d.setdefault(k, []).append(v)
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        w, h = get_image_dims(p)
        if w and h:
            add(by_name, p.name.lower(), p)
            add(by_stem, p.stem.lower(), p)
            for key in _canonical_image_stem_variants(p.stem):
                add(by_canonical_stem, key, p)
            add(by_dims, (w, h), p)
    return {"by_name": by_name, "by_stem": by_stem, "by_canonical_stem": by_canonical_stem, "by_dims": by_dims}

# -------------------- skeleton detection --------------------
class SkelInfo:
    __slots__ = (
        "path","kind","version","bytes","text","json_obj","name_tokens",
        "base_name","embedded_atlas_text","embedded_atlas_pages","embedded_atlas_regions","embedded_texture_names"
    )
    def __init__(self, path, kind, version, data_bytes=b"", text=""):
        self.path = path
        self.kind = kind
        self.version = version
        self.bytes = data_bytes
        self.text = text
        self.json_obj = None
        self.name_tokens = set()

        self.base_name = None
        self.embedded_atlas_text = None
        self.embedded_atlas_pages = None
        self.embedded_atlas_regions = None
        self.embedded_texture_names = None

def extract_json_attachment_tokens(obj) -> set[str]:
    tokens: set[str] = set()
    if not isinstance(obj, dict):
        return tokens

    def add_token(n):
        if isinstance(n, str) and n:
            tokens.add(n.lower())

    bones = obj.get("bones")
    if isinstance(bones, list):
        for b in bones:
            if isinstance(b, dict):
                add_token(b.get("name"))

    slots = obj.get("slots")
    if isinstance(slots, list):
        for s in slots:
            if isinstance(s, dict):
                add_token(s.get("name"))
                add_token(s.get("attachment"))

    def walk_skin_attachments(attachments):
        if not isinstance(attachments, dict):
            return
        for slot_name, slot_val in attachments.items():
            add_token(slot_name)
            if not isinstance(slot_val, dict):
                continue
            for attach_name in slot_val.keys():
                add_token(attach_name)

    skins = obj.get("skins")

    # Spine 3.x often stores skins as a dict keyed by skin name.
    if isinstance(skins, dict):
        for _skin_name, skin_val in skins.items():
            if not isinstance(skin_val, dict):
                continue
            walk_skin_attachments(skin_val)

    # Spine 4.x commonly stores skins as a list of objects like:
    # [{"name": "default", "attachments": {...}}, ...]
    elif isinstance(skins, list):
        for skin in skins:
            if not isinstance(skin, dict):
                continue
            add_token(skin.get("name"))
            walk_skin_attachments(skin.get("attachments"))

    return tokens

def _normalize_skeleton_json_text(raw_txt: str) -> Optional[tuple[str, dict, Optional[str]]]:
    trimmed = try_extract_skeleton_json_text(raw_txt) or raw_txt
    ok, ver = detect_spine_json_text(trimmed)
    if not ok:
        return None
    try:
        obj = json.loads(trimmed)
        if not isinstance(obj, dict):
            return None
        norm = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), indent=None)
        return norm, obj, ver
    except Exception:
        return None

def gather_skeletons_by_magic(root: Path) -> list[SkelInfo]:
    out: list[SkelInfo] = []
    for p in root.rglob("*"):
        if not p.is_file(): continue

        txt_head = read_text(p, max_bytes=2*1024*1024)
        if txt_head and ("{" in txt_head[:64] or '"bones"' in txt_head[:65536]):

            if is_cocos_spine_wrapper_text(txt_head):
                wrapper = try_parse_cocos_wrapper(txt_head)
                if wrapper:
                    skel_raw = wrapper.get("skeletonJsonStr", "")
                    norm_pack = _normalize_skeleton_json_text(skel_raw)
                    if norm_pack:
                        norm_txt, obj, ver = norm_pack
                        sk = SkelInfo(p, "json", ver, text=norm_txt)
                        sk.json_obj = obj
                        sk.name_tokens = extract_json_attachment_tokens(obj)

                        bn = wrapper.get("_name")
                        if isinstance(bn, str) and bn.strip():
                            sk.base_name = bn.strip()

                        atlas_text = wrapper.get("_atlasText")
                        if isinstance(atlas_text, str) and atlas_text.strip():
                            atlas_text = atlas_text.lstrip("\ufeff\r\n")
                            pages = parse_atlas_pages_regions(atlas_text)
                            if pages:
                                flat_regions = []
                                for pg in pages:
                                    flat_regions.extend(pg.get("regions") or [])
                                sk.embedded_atlas_text = atlas_text
                                sk.embedded_atlas_pages = pages
                                sk.embedded_atlas_regions = flat_regions

                        tnames = wrapper.get("textureNames")
                        if isinstance(tnames, list):
                            clean = [str(x) for x in tnames if isinstance(x, str) and x.strip()]
                            if clean:
                                sk.embedded_texture_names = clean

                        out.append(sk)
                        continue

            norm_pack = _normalize_skeleton_json_text(txt_head)
            if norm_pack:
                norm_txt, obj, ver = norm_pack
                sk = SkelInfo(p, "json", ver, text=read_text(p))
                try:
                    raw_txt = sk.text
                    trimmed_full = try_extract_skeleton_json_text(raw_txt) or raw_txt
                    sk.json_obj = json.loads(trimmed_full)
                    sk.name_tokens = extract_json_attachment_tokens(sk.json_obj)
                except Exception:
                    sk.json_obj = None
                    sk.name_tokens = set()
                out.append(sk)
                continue

        try:
            with p.open("rb") as f:
                head = f.read(65536)
        except Exception:
            head = b""
        if head:
            ok, ver = detect_spine_binary(head)
            if ok:
                data = read_bytes(p)
                out.append(SkelInfo(p, "binary", ver, data_bytes=data))
    return out

# -------------------- matching + ranking --------------------
def _canonical_match_stem(s: str) -> str:
    s = (s or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"\.[a-z0-9]{1,6}$", "", s)
    # Remove one or more trailing parenthetical qualifiers like
    # (front animation), (front_animation), (back), (blue), (F2), etc.
    while True:
        ns = re.sub(r"[ _-]*\([^)]*\)\s*$", "", s)
        if ns == s:
            break
        s = ns
    # Common atlas/export suffixes that often do not appear on dumped image names.
    s = re.sub(r"(?:^|[_\-\s])atlas$", "", s)
    s = re.sub(r"(?:^|[_\-\s])(ani|animation)$", "", s)
    # Normalize separators, then drop them entirely so GrandLounge,
    # grand_lounge, grand-lounge, etc. compare equally.
    s = re.sub(r"[\s\-_]+", "_", s).strip("_")
    return s.replace("_", "")


def _canonical_image_stem_variants(s: str) -> set[str]:
    base = _canonical_match_stem(s)
    out = {base} if base else set()
    if not base:
        return out

    # Allow image stems that append page indices and descriptive art names,
    # e.g. atlas: img_spine_obj_main_atlas
    # image: img_spine_obj_main_03_Obj_MainObj
    m = re.match(r"^(.*?)(\d+)([a-z].*)$", base)
    if m and m.group(1):
        out.add(m.group(1))

    # Also allow stems that end with page counters like _2/_3 or just 2/3.
    trimmed = re.sub(r"(?:_)?\d+$", "", base)
    if trimmed and trimmed != base:
        out.add(trimmed)
    return out


def _extract_page_index(label: str) -> int | None:
    stem = Path(label).stem.lower()
    if not stem:
        return None
    if stem.isdigit():
        return int(stem)
    m = re.search(r"(\d+)$", stem)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


def _atlas_family_variants(stem: str) -> list[str]:
    vals: list[str] = []
    for s in [stem, re.sub(r"_skeleton$", "", stem, flags=re.I)]:
        s = (s or "").strip()
        if s and s not in vals:
            vals.append(s)
    return vals


def _candidate_numeric_suffix_for_family(candidate_stem: str, atlas_stem: str) -> int | None:
    cand_raw = (candidate_stem or "").lower()
    cand_can = _canonical_match_stem(candidate_stem)
    for base in _atlas_family_variants(atlas_stem):
        raw = base.lower()
        can = _canonical_match_stem(base)
        for cstem, bstem in ((cand_raw, raw), (cand_can, can)):
            if not cstem or not bstem or not cstem.startswith(bstem):
                continue
            rest = cstem[len(bstem):]
            m = re.match(r"(?:_)?(\d+)(?:[a-z_ -].*)?$", rest)
            if m:
                try:
                    return int(m.group(1))
                except Exception:
                    return None
    return None


def _filter_canonical_candidates(cands, atlas_stem: str, page_name: str, prefer_dir: Path | None, prefer_same_dir: Path | None):
    if not cands:
        return []

    page_idx = _extract_page_index(page_name)
    fams = [f for f in (_canonical_match_stem(x) for x in _atlas_family_variants(atlas_stem)) if f]
    ranked = []
    for p in cands:
        cand_can = _canonical_match_stem(p.stem)
        same_family = any(cand_can.startswith(f) or f.startswith(cand_can) for f in fams)
        if not same_family:
            continue

        score = 0
        if prefer_same_dir and p.parent == prefer_same_dir:
            score += 5000
        if prefer_dir and p.parent == prefer_dir:
            score += 2000

        idx = _candidate_numeric_suffix_for_family(p.stem, atlas_stem)
        if page_idx is not None:
            if idx == page_idx:
                score += 4000
            elif idx is not None:
                score -= 1500
        ranked.append((score, p))

    ranked.sort(key=lambda x: (-x[0], len(str(x[1])), str(x[1])))
    return [p for _, p in ranked]


def _atlas_name_bonus(sk: SkelInfo, atlas_info: dict) -> int:
    sk_base = sk.base_name or sk.path.stem
    at_base = atlas_info.get("stem") or atlas_info.get("path").stem

    sk_can = _canonical_match_stem(sk_base)
    at_can = _canonical_match_stem(at_base)
    if not sk_can or not at_can:
        return 0

    if sk_can == at_can:
        return 3
    if sk_can.startswith(at_can) or at_can.startswith(sk_can):
        return 2
    if sk_can in at_can or at_can in sk_can:
        return 1
    return 0


def score_atlas_for_skeleton(sk: SkelInfo, atlas_info: dict) -> tuple[int,int,int,int]:
    region_hits = 0
    page_hits = 0

    if sk.kind == "json" and sk.name_tokens:
        token_set = sk.name_tokens
        for r in atlas_info.get("regions") or []:
            if r and r.lower() in token_set:
                region_hits += 1
        for pg in atlas_info.get("pages") or []:
            base = Path(pg["name"]).stem.lower()
            if base and base in token_set:
                page_hits += 1
    else:
        hay = (sk.bytes.lower() if sk.kind == "binary" else sk.text.encode("utf-8","ignore").lower())
        for r in atlas_info.get("regions") or []:
            rb = r.encode("utf-8","ignore").lower()
            if rb and rb in hay:
                region_hits += 1
        for pg in atlas_info.get("pages") or []:
            base = Path(pg["name"]).stem.lower().encode("ascii", errors="ignore")
            if base and base in hay:
                page_hits += 1

    name_bonus = _atlas_name_bonus(sk, atlas_info)
    dist = path_distance(sk.path.parent, atlas_info["path"].parent)
    prox = -dist
    return (region_hits, page_hits, name_bonus, prox)

def confidence_label(region_hits: int, page_hits: int, prox: int) -> str:
    if region_hits >= 10 or (region_hits >= 4 and page_hits >= 1 and prox >= -6):
        return "HIGH"
    if region_hits >= 2 or (page_hits >= 2 and prox >= -6):
        return "MED"
    return "LOW"

def rank_atlases(sk: SkelInfo, atlases: list[dict]) -> list[tuple[tuple[int,int,int], dict]]:
    scored = []
    for info in atlases:
        key = score_atlas_for_skeleton(sk, info)
        scored.append((key, info))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored

def choose_best_atlas(sk: SkelInfo, atlases: list[dict], min_hits: int, aggressive: bool) -> dict | None:
    ranked = rank_atlases(sk, atlases)
    if not ranked:
        return None
    (region_hits, page_hits, _name_bonus, prox), best = ranked[0]
    if aggressive:
        return best
    if (region_hits + page_hits) >= min_hits:
        return best
    return None

def _candidate_debug_lines(cands, prefer_dir: Path | None, prefer_same_dir: Path | None, atlas_dir: Path | None, page_label: str) -> list[str]:
    if not cands:
        return [f"      [miss] {page_label}: no candidates"]
    lines = []
    scored = []
    seen = set()
    for p in cands:
        k = _canon(p)
        if k in seen:
            continue
        seen.add(k)
        score = 0
        reasons = []
        if prefer_same_dir and p.parent == prefer_same_dir:
            score += 2000
            reasons.append("same chosen texture dir")
        if atlas_dir:
            if p.parent == atlas_dir:
                score += 1000
                reasons.append("same atlas dir")
            else:
                prox = max(0, 300 - path_distance(p.parent, atlas_dir))
                if prox:
                    score += prox
                    reasons.append(f"near atlas +{prox}")
        if prefer_dir and p.parent == prefer_dir and prefer_dir != atlas_dir:
            reasons.append("preferred dir")
        scored.append((score, p, reasons))
    scored.sort(key=lambda x: (-x[0], len(str(x[1])), str(x[1])))
    for score, p, reasons in scored[:5]:
        why = ", ".join(reasons) if reasons else "name/stem match"
        lines.append(f"      cand score={score:4d}  {p.name}  [{why}]")
    return lines

def _print_ranked_atlas_debug(sk: SkelInfo, ranked: list[tuple[tuple[int,int,int,int], dict]], top_n: int):
    print(f"[explain] atlas ranking for {sk.path.name}")
    for (rh, ph, nb, prox), info in ranked[:top_n]:
        conf = confidence_label(rh, ph, prox)
        print(f"  atlas={info['path'].name} region={rh} page={ph} name_bonus={nb} prox={prox} conf={conf}")
# -------------------- export --------------------
def _find_existing_in_out(out_root: Path, name: str) -> Path|None:
    for hit in out_root.rglob(name):
        if hit.is_file(): return hit
    return None

def _score_texture_candidate(p: Path, atlas_dir: Path|None):
    score = 0
    if atlas_dir:
        if p.parent == atlas_dir:
            score += 1000
        else:
            score += max(0, 300 - path_distance(p.parent, atlas_dir))
    return score

def _pick_best_candidate(
    cands,
    prefer_dir: Path|None,
    used: set[Path],
    allow_reuse: bool,
    *,
    prefer_nearby: bool = False,
    prefer_same_dir: Path|None = None
) -> Path|None:
    if not cands:
        return None

    pool = list(cands) if allow_reuse else [c for c in cands if c not in used]
    if not pool:
        return None

    scored = []
    for p in pool:
        s = 0
        if prefer_same_dir and p.parent == prefer_same_dir:
            s += 2000
        if prefer_nearby:
            s += _score_texture_candidate(p, prefer_dir)
        scored.append((s, p))

    scored.sort(key=lambda x: (-x[0], len(str(x[1])), str(x[1])))
    return scored[0][1]

def _derive_page_base_name(pages: list[dict]) -> str | None:
    stems = [Path(pg.get("name", "")).stem for pg in (pages or []) if pg.get("name")]
    if not stems:
        return None
    base = stems[0]
    if not base:
        return None
    for s in stems[1:]:
        if s.startswith(base):
            tail = s[len(base):]
        else:
            alt = re.sub(r"_\d+$", "", s)
            if alt != base:
                return None
            tail = s[len(alt):]
        if tail and not re.fullmatch(r"(?:_)?\d+", tail):
            return None
    return base


def _infer_page_ext(page_name: str, pages: list[dict]) -> str:
    page_suffix = Path(page_name).suffix
    if page_suffix:
        return page_suffix
    for pg in (pages or []):
        suffix = Path(pg.get("name", "")).suffix
        if suffix:
            return suffix
    return ".png"


def _strip_skeleton_suffix(atlas_stem: str) -> str:
    return atlas_stem[:-len("_skeleton")] if atlas_stem.lower().endswith("_skeleton") else atlas_stem


def _numeric_page_family_bases(atlas_stem: str) -> list[str]:
    """
    Candidate family bases for numeric atlas pages like 0/1/2.

    Maple sometimes stores the atlas as:
        ..._kCity_6_sinkhole.atlas
    but the first page texture as:
        ..._kCity_6_0.png

    So for numeric page names we try both:
      1) the plain atlas-derived base (after stripping _skeleton)
      2) a family base with a trailing art label removed when the atlas stem ends
         with ..._<number>_<label>
    """
    base = _strip_skeleton_suffix(atlas_stem)
    out: list[str] = []

    def add(s: str):
        if s and s not in out:
            out.append(s)

    # Prefer the numeric family form first when present, e.g.
    # ..._kCity_6_sinkhole -> ..._kCity_6
    m = re.match(r"^(.*?_\d+)(?:_[A-Za-z][A-Za-z0-9]*)+$", base)
    if m:
        add(m.group(1))

    # Fallback to the plain atlas-derived base.
    add(base)
    return out


def _map_generic_page_to_local_names(page_name: str, atlas_stem: str, page_base: str | None, pages: list[dict] | None = None) -> list[str]:
    page_path = Path(page_name)
    page_stem = page_path.stem
    page_ext = _infer_page_ext(page_name, pages or [])
    out: list[str] = []

    def add(name: str | None):
        if name and name not in out:
            out.append(name)

    # Numeric atlas pages like 0 / 1 / 2. Prefer a family base stripped down to
    # ..._<number> before falling back to the full atlas stem.
    if page_stem.isdigit():
        for base in _numeric_page_family_bases(atlas_stem):
            add(f"{base}_{page_stem}{page_ext}")
        return out

    # Generic Spine pages like skeleton.png / skeleton2.png / skeleton_2.png.
    if page_base:
        norm_page = re.sub(r"_", "", page_stem.lower())
        norm_base = re.sub(r"_", "", page_base.lower())
        if norm_page.startswith(norm_base):
            suffix = page_stem[len(page_base):] if page_stem.startswith(page_base) else page_stem[len(page_base.replace("_", "")):]
            if suffix:
                add(f"{atlas_stem}{suffix}{page_ext}")
            else:
                add(f"{atlas_stem}{page_ext}")

    # Page names that are already an alternate generic convention like page0/page1.
    m = re.fullmatch(r"([a-zA-Z_\-]*?)(\d+)", page_stem)
    if m:
        num = m.group(2)
        for base in _numeric_page_family_bases(atlas_stem):
            add(f"{base}_{num}{page_ext}")

    return out


def rewrite_atlas_page_names(original_text: str, new_names: list[str]) -> str:
    blocks = re.split(r"(\r?\n\s*\r?\n)", original_text)
    new_chunks = []
    page_idx = 0
    for i in range(0, len(blocks), 2):
        sec = blocks[i]
        sep = blocks[i+1] if i+1 < len(blocks) else ""
        lines = sec.splitlines()
        if lines and page_idx < len(new_names):
            j = 0
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                lines[j] = new_names[page_idx]
                page_idx += 1
        new_chunks.append("\n".join(lines))
        new_chunks.append(sep)
    return "".join(new_chunks)


def _ensure_unique_output_filenames(names: list[str]) -> list[str]:
    """Ensure atlas page filenames are unique within one built set.

    This matters for multipage atlases when ambiguous matching or rewrite mode would
    otherwise assign the same source image name to multiple atlas pages, collapsing
    several page references down to one physical file on disk.
    """
    seen: dict[str, int] = {}
    out: list[str] = []
    for raw in names:
        base_name = Path(raw).name or "page.png"
        stem = Path(base_name).stem
        suffix = Path(base_name).suffix or ".png"
        key = base_name.lower()
        if key not in seen:
            seen[key] = 1
            out.append(base_name)
            continue

        idx = seen[key]
        while True:
            candidate = f"{stem}__dup{idx}{suffix}"
            ckey = candidate.lower()
            if ckey not in seen:
                seen[key] += 1
                seen[ckey] = 1
                out.append(candidate)
                break
            idx += 1
    return out



def _rank_candidate_pool(cands, prefer_dir: Path | None, prefer_same_dir: Path | None):
    seen = set()
    scored = []
    for p in cands or []:
        key = _canon(p)
        if key in seen:
            continue
        seen.add(key)
        score = 0
        reasons = []
        if prefer_same_dir and p.parent == prefer_same_dir:
            score += 5000
            reasons.append("same chosen texture dir")
        if prefer_dir:
            if p.parent == prefer_dir:
                score += 2000
                reasons.append("same atlas dir")
            else:
                prox = max(0, 500 - path_distance(p.parent, prefer_dir))
                if prox:
                    score += prox
                    reasons.append(f"near atlas +{prox}")
        scored.append((score, p, reasons))
    scored.sort(key=lambda x: (-x[0], len(str(x[1])), str(x[1])))
    return scored

def _stage_page_dimension_candidates(
    dst_dir: Path,
    page_idx: int,
    page_name: str,
    want_w,
    want_h,
    tex_lut: dict,
    prefer_dir: Path | None,
    prefer_same_dir: Path | None,
    move: bool,
    link_mode: str,
    limit: int,
):
    if not want_w or not want_h:
        return 0

    by_dims = tex_lut["by_dims"]
    pool = by_dims.get((want_w, want_h)) or []
    pool_count = len(pool)
    ranked = _rank_candidate_pool(pool, prefer_dir, prefer_same_dir)
    ranked_count = len(ranked)
    if limit and limit > 0:
        ranked = ranked[:limit]
    if not ranked:
        print(f"    [stage] page {page_idx}: {page_name} {want_w}x{want_h}: no candidates (pool={pool_count}, unique={ranked_count}, limit={limit})")
        return 0

    safe_page = re.sub(r'[^A-Za-z0-9._-]+', '_', Path(page_name).name).strip('_') or f"page{page_idx}"
    cand_root = dst_dir / "_candidates" / f"page{page_idx:02d}_{safe_page}_{want_w}x{want_h}"
    cand_root.mkdir(parents=True, exist_ok=True)

    manifest_lines = [
        f"page_index: {page_idx}",
        f"atlas_page: {page_name}",
        f"requested_size: {want_w}x{want_h}",
        f"candidate_pool_count: {pool_count}",
        f"candidate_unique_count: {ranked_count}",
        f"candidate_limit: {limit} (0 = no limit)",
        f"candidate_count: {len(ranked)}",
        "",
    ]

    placed = 0
    for rank, (score, p, reasons) in enumerate(ranked, 1):
        ext = p.suffix or ".png"
        label = re.sub(r'[^A-Za-z0-9._-]+', '_', p.stem).strip('_') or "texture"
        dst_name = f"{rank:04d}_score{score}_{label}{ext}"
        dst = cand_root / dst_name
        if copy_or_move(p, dst, move, link_mode=link_mode):
            placed += 1
            why = ", ".join(reasons) if reasons else "dimension match"
            manifest_lines.append(f"{rank:04d}  score={score:5d}  src={p}  [{why}]")

    (cand_root / "_manifest.txt").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8", newline="\n")
    limit_note = "unlimited" if not limit or limit <= 0 else str(limit)
    print(f"    [stage] page {page_idx}: {page_name} {want_w}x{want_h}: staged {placed}/{ranked_count} unique candidates (pool={pool_count}, limit={limit_note})")
    return placed

def export_set(
    sk: SkelInfo,
    atlas_info: dict | None,
    tex_lut: dict,
    out_root: Path,
    move: bool,
    link_mode: str,
    allow_reuse_textures: bool,
    dims_fallback: bool,
    prefer_nearby_textures: bool,
    prefer_consistent_texture_dir: bool,
    rewrite_pages_to_match_source: bool,
    explain_match: bool,
    dedupe_textures: bool,
    stage_dim_candidates: bool,
    stage_dim_candidates_limit: int,
):
    # If no atlas matched on disk but the skeleton has an embedded atlas, use it
    if not atlas_info and sk.embedded_atlas_text and sk.embedded_atlas_pages:
        atlas_info = {
            "path": sk.path,
            "pages": sk.embedded_atlas_pages,
            "stem": (sk.base_name or sk.path.stem),
            "text": sk.embedded_atlas_text,
            "regions": (sk.embedded_atlas_regions or []),
        }

    if atlas_info:
        base_stem = atlas_info["path"].name[:-10] if atlas_info["path"].name.lower().endswith(".atlas.txt") else atlas_info["path"].stem
    else:
        base_stem = sk.path.stem

    if sk.base_name and sk.base_name.strip():
        base_stem = sk.base_name.strip()

    dst_dir = unique_output_dir(out_root, base_stem, sk.path)

    # skeleton write
    if sk.kind == "binary":
        raw = sk.bytes or read_bytes(sk.path)
        try:
            guess = raw.decode("utf-8", errors="ignore")
        except Exception:
            guess = ""
        maybe_json = try_extract_skeleton_json_text(guess) if guess else None
        if maybe_json:
            (dst_dir / f"{base_stem}.json").write_text(maybe_json, encoding="utf-8", newline="\n")
        else:
            (dst_dir / f"{base_stem}.skel").write_bytes(raw)
    else:
        raw_txt = sk.text if sk.text else read_text(sk.path)
        trimmed = try_extract_skeleton_json_text(raw_txt) or raw_txt
        (dst_dir / f"{base_stem}.json").write_text(trimmed, encoding="utf-8", newline="\n")

    if not atlas_info:
        print(f"    [note] no atlas matched for {sk.path.name}")
        return

    pages = atlas_info["pages"]
    prefer_dir = atlas_info["path"].parent if isinstance(atlas_info.get("path"), Path) else None
    atlas_stem_for_pages = atlas_info.get("stem") or base_stem
    atlas_page_base = _derive_page_base_name(pages)

    used = set()
    chosen_texture_dir = None

    by_name, by_stem, by_canonical_stem, by_dims = tex_lut["by_name"], tex_lut["by_stem"], tex_lut["by_canonical_stem"], tex_lut["by_dims"]

    assigned_paths = []
    assigned_reasons = []
    missed_pages = []
    dim_fallback_pages = []
    for idx, pg in enumerate(pages):
        debug_notes = []
        page = pg["name"]
        want_w, want_h = pg.get("size") or (None, None)
        chosen = None
        chosen_reason = None

        prefer_same = chosen_texture_dir if prefer_consistent_texture_dir else None

        # 0) If wrapper has textureNames, try those first
        if sk.embedded_texture_names and idx < len(sk.embedded_texture_names):
            hinted = Path(sk.embedded_texture_names[idx]).name

            if hinted.lower() in by_name:
                if explain_match:
                    debug_notes.append(f"wrapper textureNames exact filename hint: {hinted}")
                    debug_notes.extend(_candidate_debug_lines(by_name[hinted.lower()], prefer_dir, prefer_same, prefer_dir, page))
                chosen = _pick_best_candidate(
                    by_name[hinted.lower()],
                    prefer_dir,
                    used,
                    allow_reuse_textures,
                    prefer_nearby=prefer_nearby_textures,
                    prefer_same_dir=prefer_same,
                )
                if chosen:
                    chosen_reason = "wrapper_name"

            if not chosen:
                hinted_stem = Path(hinted).stem.lower()
                if hinted_stem in by_stem:
                    if explain_match:
                        debug_notes.append(f"wrapper textureNames stem hint: {hinted_stem}")
                        debug_notes.extend(_candidate_debug_lines(by_stem[hinted_stem], prefer_dir, prefer_same, prefer_dir, page))
                    chosen = _pick_best_candidate(
                        by_stem[hinted_stem],
                        prefer_dir,
                        used,
                        allow_reuse_textures,
                        prefer_nearby=prefer_nearby_textures,
                        prefer_same_dir=prefer_same,
                    )
                    if chosen:
                        chosen_reason = "wrapper_stem"

        # 1) Optional generic multipage remap (e.g. atlas says skeleton2.png, dumped file is MyLongStem2.png)
        if not chosen and rewrite_pages_to_match_source:
            mapped_names = []
            for mapped_local in _map_generic_page_to_local_names(page, atlas_stem_for_pages, atlas_page_base, pages):
                if mapped_local not in mapped_names:
                    mapped_names.append(mapped_local)
                alt_stem = _canonical_match_stem(Path(mapped_local).stem)
                if alt_stem and alt_stem != Path(mapped_local).stem.lower():
                    alt_name = alt_stem + Path(mapped_local).suffix
                    if alt_name not in mapped_names:
                        mapped_names.append(alt_name)
            for mapped_try in mapped_names:
                if mapped_try.lower() in by_name:
                    if explain_match:
                        debug_notes.append(f"rewrite-pages mapped page -> {mapped_try}")
                        debug_notes.extend(_candidate_debug_lines(by_name[mapped_try.lower()], prefer_dir, prefer_same, prefer_dir, page))
                    chosen = _pick_best_candidate(
                        by_name[mapped_try.lower()],
                        prefer_dir,
                        used,
                        allow_reuse_textures,
                        prefer_nearby=prefer_nearby_textures,
                        prefer_same_dir=prefer_same,
                    )
                    if chosen:
                        chosen_reason = "rewrite"
                        break

        # 1b) Exact page filename match
        if not chosen and page.lower() in by_name:
            if explain_match:
                debug_notes.append(f"exact page filename match: {page.lower()}")
                debug_notes.extend(_candidate_debug_lines(by_name[page.lower()], prefer_dir, prefer_same, prefer_dir, page))
            chosen = _pick_best_candidate(
                by_name[page.lower()],
                prefer_dir,
                used,
                allow_reuse_textures,
                prefer_nearby=prefer_nearby_textures,
                prefer_same_dir=prefer_same,
            )
            if chosen:
                chosen_reason = "exact"

        # 2) Page stem match
        if not chosen:
            stem = Path(page).stem.lower()
            if stem in by_stem:
                if explain_match:
                    debug_notes.append(f"page stem match: {stem}")
                    debug_notes.extend(_candidate_debug_lines(by_stem[stem], prefer_dir, prefer_same, prefer_dir, page))
                chosen = _pick_best_candidate(
                    by_stem[stem],
                    prefer_dir,
                    used,
                    allow_reuse_textures,
                    prefer_nearby=prefer_nearby_textures,
                    prefer_same_dir=prefer_same,
                )
                if chosen:
                    chosen_reason = "stem"

        # 2b) Canonical stem fallback for Maple naming drift (_atlas, parenthetical
        # qualifiers, inserted numeric/art-name tokens, underscore/case variance).
        # Keep this conservative so it does not override cleaner v3-style exact/stem matches.
        if not chosen:
            page_can = _canonical_match_stem(Path(page).stem)
            atlas_can = _canonical_match_stem(atlas_stem_for_pages)
            for can_key in [k for k in (page_can, atlas_can) if k]:
                if can_key in by_canonical_stem:
                    filt = _filter_canonical_candidates(by_canonical_stem[can_key], atlas_stem_for_pages, page, prefer_dir, prefer_same)
                    if explain_match:
                        debug_notes.append(f"canonical stem fallback: {can_key}")
                        debug_notes.extend(_candidate_debug_lines(filt or by_canonical_stem[can_key], prefer_dir, prefer_same, prefer_dir, page))
                    # Only trust broad canonical fallback when it narrows to a manageable family.
                    if filt and (len(filt) <= 4 or (prefer_same and any(p.parent == prefer_same for p in filt)) or (prefer_dir and any(p.parent == prefer_dir for p in filt))):
                        chosen = _pick_best_candidate(
                            filt,
                            prefer_dir,
                            used,
                            allow_reuse_textures,
                            prefer_nearby=prefer_nearby_textures,
                            prefer_same_dir=prefer_same,
                        )
                        if chosen:
                            chosen_reason = "canonical"
                    if chosen:
                        break

        used_dim_fallback = False

        # 3) Dimension fallback
        if not chosen and dims_fallback and want_w and want_h and (want_w, want_h) in by_dims:
            if explain_match:
                debug_notes.append(f"dimension fallback: {want_w}x{want_h}")
                debug_notes.extend(_candidate_debug_lines(by_dims[(want_w, want_h)], prefer_dir, prefer_same, prefer_dir, page))
            dims_pool = by_dims[(want_w, want_h)]
            # For multipage atlases, dimension-only fallback should prefer different
            # source files across pages when possible; otherwise several atlas pages
            # can collapse to one physical filename in the built set.
            dims_allow_reuse = allow_reuse_textures
            if len(pages) > 1:
                dims_allow_reuse = False
            chosen = _pick_best_candidate(
                dims_pool,
                prefer_dir,
                used,
                dims_allow_reuse,
                prefer_nearby=prefer_nearby_textures,
                prefer_same_dir=prefer_same,
            )
            if chosen:
                chosen_reason = "dims"
            if chosen:
                used_dim_fallback = True

        # 4) Already placed in output somewhere
        if not chosen:
            existing = _find_existing_in_out(out_root, page)
            if existing:
                chosen = existing
                chosen_reason = "existing"

        # Lock in consistency after the first successful choice
        if chosen and prefer_consistent_texture_dir and not chosen_texture_dir:
            chosen_texture_dir = chosen.parent

        if chosen and (not allow_reuse_textures or chosen_reason == "dims"):
            used.add(chosen)

        assigned_paths.append(chosen)
        assigned_reasons.append(chosen_reason)
        if not isinstance(chosen, Path):
            missed_pages.append((idx + 1, page, want_w, want_h, prefer_same))
        elif stage_dim_candidates and used_dim_fallback and want_w and want_h:
            pool = tex_lut["by_dims"].get((want_w, want_h)) or []
            if len(pool) > 1:
                dim_fallback_pages.append((idx + 1, page, want_w, want_h, prefer_same))
        if explain_match:
            print(f"    [page] {page} -> {(chosen.name if isinstance(chosen, Path) else 'MISS')}")
            for note in debug_notes[:8]:
                print(f"      {note}")

    # rewrite atlas page names
    new_page_names = []
    for i, chosen in enumerate(assigned_paths, 1):
        fallback_ext = Path(pages[i-1]["name"]).suffix or ".png"
        if rewrite_pages_to_match_source and isinstance(chosen, Path):
            new_page_names.append(chosen.name)
        else:
            ext = chosen.suffix if isinstance(chosen, Path) and chosen.suffix else fallback_ext
            new_page_names.append(f"{base_stem}_page{i}{ext}")

    new_page_names = _ensure_unique_output_filenames(new_page_names)

    atlas_dst = dst_dir / (base_stem + ".atlas")
    atlas_dst.write_text(rewrite_atlas_page_names(atlas_info["text"], new_page_names), encoding="utf-8", newline="\n")

    # copy/move textures
    placed = 0
    deduped = 0
    for i, chosen in enumerate(assigned_paths, 1):
        if not isinstance(chosen, Path):
            continue
        dst = dst_dir / new_page_names[i-1]

        if dedupe_textures and not move:
            digest = sha1_file(chosen)
            existing_dst = OUTPUT_DEDUPE.get(digest) if digest else None
            if existing_dst and Path(existing_dst).exists() and _canon(Path(existing_dst)) != _canon(dst):
                if place_deduped(Path(existing_dst), dst, link_mode):
                    placed += 1
                    deduped += 1
                    if explain_match:
                        print(f"    [dedupe] {dst.name} linked to existing {Path(existing_dst).name}")
                    continue

        if copy_or_move(chosen, dst, move, link_mode=link_mode):
            placed += 1
            if dedupe_textures and not move:
                digest = sha1_file(chosen)
                if digest and dst.exists():
                    OUTPUT_DEDUPE.setdefault(digest, str(dst))

    staged = 0
    staged_page_specs = []
    if stage_dim_candidates:
        staged_page_specs.extend(missed_pages)
        for spec in dim_fallback_pages:
            if spec not in staged_page_specs:
                staged_page_specs.append(spec)

    if stage_dim_candidates and staged_page_specs:
        for page_idx, page_name, want_w, want_h, prefer_same in staged_page_specs:
            staged += _stage_page_dimension_candidates(
                dst_dir=dst_dir,
                page_idx=page_idx,
                page_name=page_name,
                want_w=want_w,
                want_h=want_h,
                tex_lut=tex_lut,
                prefer_dir=prefer_dir,
                prefer_same_dir=(prefer_same if prefer_consistent_texture_dir else None),
                move=move,
                link_mode=link_mode,
                limit=stage_dim_candidates_limit,
            )

    if dedupe_textures and not move:
        print(f"    textures: {placed}/{len(pages)} placed ({deduped} deduped)")
    else:
        print(f"    textures: {placed}/{len(pages)} placed")
    if stage_dim_candidates and staged_page_specs:
        print(f"    staged candidates: {staged} links/files across {len(staged_page_specs)} page(s) ({len(missed_pages)} unresolved, {len(dim_fallback_pages)} resolved-via-dims)")



def _find_single_atlas_in_built_set(set_dir: Path) -> Path:
    atlases = sorted([p for p in set_dir.glob('*.atlas') if p.is_file()], key=lambda x: x.name.lower())
    if not atlases:
        raise FileNotFoundError(f"No .atlas file found in built set: {set_dir}")
    if len(atlases) > 1:
        raise RuntimeError(f"Expected one .atlas file in built set, found {len(atlases)} in {set_dir}")
    return atlases[0]


def _candidate_folder_for_page(set_dir: Path, page_idx: int) -> Path:
    cand_base = set_dir / '_candidates'
    if not cand_base.is_dir():
        raise FileNotFoundError(f"No _candidates folder found in built set: {set_dir}")
    prefix = f"page{page_idx:02d}_"
    matches = sorted([p for p in cand_base.iterdir() if p.is_dir() and p.name.startswith(prefix)], key=lambda x: x.name.lower())
    if not matches:
        raise FileNotFoundError(f"No candidate folder found for atlas page {page_idx} in {cand_base}")
    if len(matches) > 1:
        raise RuntimeError(f"Multiple candidate folders found for atlas page {page_idx} in {cand_base}")
    return matches[0]


def _candidate_file_by_rank(cand_dir: Path, rank: int) -> Path:
    prefix = f"{rank:04d}_"
    matches = sorted([p for p in cand_dir.iterdir() if p.is_file() and p.name.startswith(prefix) and p.name != '_manifest.txt'], key=lambda x: x.name.lower())
    if not matches:
        raise FileNotFoundError(f"No candidate file with rank {rank} found in {cand_dir}")
    return matches[0]


def materialize_candidate_into_built_set(
    set_dir: Path,
    page_idx: int,
    candidate_rank: int,
    link_mode: str,
):
    set_dir = set_dir.resolve()
    atlas_path = _find_single_atlas_in_built_set(set_dir)
    atlas_text = read_text(atlas_path)
    atlas_pages = parse_atlas_pages_regions(atlas_text)
    if not atlas_pages:
        raise RuntimeError(f"Could not parse atlas pages from {atlas_path}")
    if page_idx < 1 or page_idx > len(atlas_pages):
        raise IndexError(f"page index {page_idx} is out of range; atlas has {len(atlas_pages)} page(s)")

    cand_dir = _candidate_folder_for_page(set_dir, page_idx)
    cand_file = _candidate_file_by_rank(cand_dir, candidate_rank)

    target_name = Path(atlas_pages[page_idx - 1]['name']).name
    if not target_name:
        raise RuntimeError(f"Atlas page {page_idx} in {atlas_path.name} does not have a usable filename")
    target_path = set_dir / target_name

    # Archive any prior materialized file so repeated trials are reversible and visible.
    archive_dir = set_dir / '_materialized_history' / f'page{page_idx:02d}'
    archive_dir.mkdir(parents=True, exist_ok=True)
    if target_path.exists() or target_path.is_symlink():
        prior_archive = archive_dir / f"previous_{target_path.name}"
        try:
            if prior_archive.exists() or prior_archive.is_symlink():
                if prior_archive.is_dir():
                    shutil.rmtree(prior_archive)
                else:
                    prior_archive.unlink()
        except Exception:
            pass
        try:
            target_path.replace(prior_archive)
        except Exception:
            try:
                if target_path.is_dir():
                    shutil.rmtree(target_path)
                else:
                    target_path.unlink()
            except Exception as e:
                raise RuntimeError(f"Could not clear existing target page file {target_path}: {e}")

    if not link_or_copy(cand_file, target_path, link_mode):
        raise RuntimeError(f"Failed to materialize candidate {cand_file} -> {target_path}")

    choice_note = [
        f"atlas: {atlas_path.name}",
        f"page_index: {page_idx}",
        f"target_name: {target_name}",
        f"candidate_rank: {candidate_rank}",
        f"candidate_source: {cand_file}",
        f"placement_mode: {link_mode}",
    ]
    note_path = set_dir / '_materialized_history' / f'page{page_idx:02d}_current.txt'
    note_path.write_text("\n".join(choice_note) + "\n", encoding='utf-8', newline='\n')

    print(f"[materialized] set={set_dir}")
    print(f"  atlas:      {atlas_path.name}")
    print(f"  page:       {page_idx}")
    print(f"  target:     {target_name}")
    print(f"  candidate:  rank {candidate_rank} -> {cand_file.name}")
    print(f"  placed via: {link_mode}")


# -------------------- main --------------------
def main():
    ap = argparse.ArgumentParser(description="Scan recursively for Spine assets and build normalized sets.")
    ap.add_argument("--root", help="Root folder to scan (recursively).")
    ap.add_argument("--materialize-built-set", help="Path to one already-built spine set containing a .atlas and a _candidates folder. Used to materialize one candidate page into the set for rapid viewer testing.")
    ap.add_argument("--materialize-page", type=int, help="1-based atlas page index to materialize inside --materialize-built-set.")
    ap.add_argument("--materialize-candidate", type=int, help="Candidate rank to activate from that page's _candidates folder (for example 1, 2, 3...).")

    ap.add_argument("--move", action="store_true", help="Move files into the build instead of copying.")
    ap.add_argument("--link-mode", choices=("copy","symlink","hardlink"), default="copy",
        help="How to place texture pages into each built folder: copy (default), symlink, or hardlink. "
             "If linking fails (common on Windows without Developer Mode/Admin), it falls back to copy.")
    ap.add_argument("--min-hits", type=int, default=1, help="Minimum (region+page) hits to accept an atlas match (default: 1).")
    ap.add_argument("--aggressive-atlas", action="store_true", help="Choose best atlas even if hit score is below --min-hits.")
    ap.add_argument("--top-n", type=int, default=1, help="If >0, print top-N atlas candidates per skeleton with scores.")
    ap.add_argument("--allow-reuse-textures", action="store_false", help="Allow the same texture file to be reused (less conservative).")
    ap.add_argument("--dims-fallback", action="store_true", help="Allow dimension-only texture selection when names fail (more risk).")

    ap.add_argument("--prefer-nearby-textures", action="store_true",
        help="Prefer textures closer to the atlas directory when names are ambiguous.")
    ap.add_argument("--prefer-consistent-texture-dir", action="store_true",
        help="Prefer placing all atlas pages from the same texture directory when possible.")
    ap.add_argument("--rewrite-pages-to-match-source", action="store_true",
        help="For generic multipage atlases (for example skeleton.png, skeleton2.png, 0, 1, page0, ...), prefer source images named after the atlas stem and rewrite atlas page names to match those source filenames.")
    ap.add_argument("--explain-match", action="store_true",
        help="Print atlas ranking details and page-level texture match decisions for debugging edge cases.")
    ap.add_argument("--dedupe-textures", action="store_true",
        help="Deduplicate identical placed textures across output sets by linking later duplicates to the first placed copy. Best used with --link-mode symlink or hardlink. Ignored with --move.")
    ap.add_argument("--stage-dim-candidates", action="store_true",
        help="For unresolved atlas pages, stage all same-dimension texture candidates into a _candidates folder inside that built set. Best used with --link-mode symlink or hardlink.")
    ap.add_argument("--stage-dim-candidates-limit", type=int, default=250,
        help="Maximum number of same-dimension candidates to stage per unresolved page (default: 250, 0 = no limit).")
    ap.add_argument(
        "--entity-mode",
        choices=("off", "childdirs"),
        default="off",
        help="off = scan --root recursively as one corpus; childdirs = treat each immediate child directory of --root as its own isolated Spine entity root."
    )
    args = ap.parse_args()

    if args.materialize_built_set:
        if args.root:
            print('[info] ignoring --root because --materialize-built-set was provided.')
        if args.move:
            print('[warn] --move is ignored in materialize mode; the selected candidate is linked/copied into place.')
        if args.materialize_page is None or args.materialize_candidate is None:
            ap.error('--materialize-built-set requires both --materialize-page and --materialize-candidate')
        materialize_candidate_into_built_set(
            set_dir=Path(args.materialize_built_set),
            page_idx=args.materialize_page,
            candidate_rank=args.materialize_candidate,
            link_mode=args.link_mode,
        )
        return

    if not args.root:
        ap.error('--root is required unless --materialize-built-set is used')

    root = Path(args.root).resolve()
    if args.link_mode != "copy" and args.move:
        print("[warn] --move overrides --link-mode (textures will be moved, not linked).")
    elif args.link_mode != "copy":
        print(f"[info] texture placement mode: {args.link_mode}")
    def build_output_name(root: Path) -> str:
        def clean(s: str) -> str:
            return re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_')

        parts = []

        # Build ONE shared container from the parent context of the scanned folder.
        # Example:
        #   root = ...\Kinesis\Spine\FolderA
        #   container = Spine_Built-Kinesis_Spine
        if root.parent and root.parent.parent and root.parent.parent.name:
            parts.append(clean(root.parent.parent.name))
        if root.parent and root.parent.name:
            parts.append(clean(root.parent.name))

        parts = [p for p in parts if p]

        if parts:
            return f"Spine_Built-{'_'.join(parts)}"
        return "Spine_Built"

    container_name = build_output_name(root)
    container_root = (root.parent / container_name).resolve()
    container_root.mkdir(parents=True, exist_ok=True)

    run_folder_name = re.sub(r'[^A-Za-z0-9]+', '_', root.name).strip('_')
    if not run_folder_name:
        run_folder_name = "Spine"

    out_root = (container_root / run_folder_name).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    if args.entity_mode == "childdirs":
        entity_roots = find_entity_roots(root)
        print(f"[info] entity roots detected: {len(entity_roots)}")

        if not entity_roots:
            print("[warn] no child entity folders detected; falling back to normal recursive mode.")
        else:
            total_built = 0
            total_matched = 0
            total_skels = 0

            for ent_root in entity_roots:
                run_folder_name = re.sub(r'[^A-Za-z0-9]+', '_', ent_root.name).strip('_')
                if not run_folder_name:
                    run_folder_name = "Spine"

                entity_out_root = (container_root / run_folder_name).resolve()
                entity_out_root.mkdir(parents=True, exist_ok=True)

                b, m, t = process_one_root(ent_root, entity_out_root, args)
                total_built += b
                total_matched += m
                total_skels += t

            print(f"\n[done] built: {total_built}/{total_skels}   atlas matched/embedded: {total_matched}/{total_skels}")
            return

    print(f"[info] scanning for atlases…")
    atlases = index_atlases_by_magic(root)
    print(f"[info] atlases detected: {len(atlases)}")

    print(f"[info] indexing textures…")
    tex_lut = build_texture_lookup(root)

    print(f"[info] scanning for skeletons…")
    skels = gather_skeletons_by_magic(root)
    print(f"[info] skeleton candidates: {len(skels)}")

    built = 0
    matched = 0

    for sk in skels:
        ranked = rank_atlases(sk, atlases)
        best = choose_best_atlas(sk, atlases, args.min_hits, args.aggressive_atlas)

        if args.top_n > 0 and ranked:
            print(f"\n[top] {sk.path.name}")
            for (rh, ph, nb, prox), info in ranked[:args.top_n]:
                conf = confidence_label(rh, ph, prox)
                print(f"  - {info['path'].name}  region={rh} page={ph} name_bonus={nb} prox={prox}  conf={conf}")
        if args.explain_match and ranked:
            _print_ranked_atlas_debug(sk, ranked, max(args.top_n, 5))

        try:
            export_set(
                sk, best, tex_lut, out_root,
                move=args.move,
                link_mode=args.link_mode,
                allow_reuse_textures=args.allow_reuse_textures,
                dims_fallback=args.dims_fallback,
                prefer_nearby_textures=args.prefer_nearby_textures,
                prefer_consistent_texture_dir=args.prefer_consistent_texture_dir,
                rewrite_pages_to_match_source=args.rewrite_pages_to_match_source,
                explain_match=args.explain_match,
                dedupe_textures=args.dedupe_textures,
                stage_dim_candidates=args.stage_dim_candidates,
                stage_dim_candidates_limit=args.stage_dim_candidates_limit,
            )
            built += 1

            if best or (sk.embedded_atlas_text and sk.embedded_atlas_pages):
                matched += 1
                if best:
                    rh, ph, _nb, prox = score_atlas_for_skeleton(sk, best)
                    conf = confidence_label(rh, ph, prox)
                    print(f"[OK] {sk.path.name} -> {best['path'].name} (region={rh}, page={ph}, prox={prox}, conf={conf})")
                else:
                    print(f"[OK] {sk.path.name} -> (embedded atlas)")
            else:
                print(f"[OK] {sk.path.name} -> (no atlas; skeleton normalized)")
        except Exception as e:
            print(f"[WARN] failed set for {sk.path.name}: {e}")

    print(f"\n[done] built: {built}/{len(skels)}   atlas matched/embedded: {matched}/{len(skels)}")

if __name__ == "__main__":
    main()
