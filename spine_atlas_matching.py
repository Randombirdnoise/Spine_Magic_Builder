"""Shared, non-consuming skeleton/atlas matching and auditable candidate exports.

Binary evidence validates encoded string lengths, not attachment semantics. It is
deliberately labelled heuristic: bones can share names with texture regions.
"""
from collections import Counter, deque
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re


def attachment_paths(obj):
    """Read texture lookups, excluding bones, slots and non-rendering attachments."""
    result = set()
    if not isinstance(obj, dict):
        return result
    skins = obj.get("skins", {})
    if isinstance(skins, dict):
        groups = skins.values()
    elif isinstance(skins, list):
        groups = [s.get("attachments", {}) for s in skins if isinstance(s, dict)]
    else:
        groups = []
    for slots in groups:
        if not isinstance(slots, dict):
            continue
        for attachments in slots.values():
            if not isinstance(attachments, dict):
                continue
            for key, data in attachments.items():
                if not isinstance(data, dict):
                    continue
                if data.get("type", "region") not in (
                    "region", "mesh", "linkedmesh", "skinnedmesh", "weightedmesh",
                ):
                    continue
                name = data.get("path") or data.get("name") or key
                if not isinstance(name, str) or not name:
                    continue
                sequence = data.get("sequence")
                if isinstance(sequence, dict) and isinstance(sequence.get("count"), int):
                    start = sequence.get("start", 1)
                    digits = sequence.get("digits", 0)
                    if isinstance(start, int) and isinstance(digits, int) and digits >= 0:
                        for i in range(sequence["count"]):
                            result.add(name + str(start + i).zfill(digits))
                        continue
                result.add(name)
    return result


def varint(value):
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def shared_strings_38(data):
    """Read only the documented 3.8 header/string table, never guess offsets.

    None means unsupported/truncated data: callers retain the generic fallback.
    This is not an attachment or animation parser and makes no renderability claim.
    """
    offset = 0

    def integer():
        nonlocal offset
        value = 0
        for shift in range(0, 35, 7):
            if offset >= len(data):
                raise ValueError("truncated varint")
            byte = data[offset]
            offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError("invalid varint")

    def string():
        nonlocal offset
        size = integer()
        if size <= 1:
            return ""
        end = offset + size - 1
        if end > len(data):
            raise ValueError("truncated string")
        value = data[offset:end].decode("utf-8")
        offset = end
        return value

    try:
        string()  # Export hash.
        if not re.fullmatch(r"3\.8\.\d+", string()):
            return None
        offset += 16  # x/y/width/height, four big-endian floats.
        if offset >= len(data) or data[offset] not in (0, 1):
            return None
        nonessential = data[offset]
        offset += 1
        if nonessential:
            offset += 4  # fps
            string()  # Images path.
            string()  # Audio path.
        count = integer()
        if count > len(data) - offset:
            return None
        strings = {string() for _ in range(count)}
        # A valid skeleton must still have its bone count/data after the table.
        if offset >= len(data):
            return None
        return strings - {""}
    except (ValueError, UnicodeError, IndexError):
        return None


def path_key(path):
    return (str(path).casefold(), str(path))


class BytePatternIndex:
    """Aho-Corasick byte matcher: one pass, including overlapping/prefix hits.

    Avoid scanning a multi-megabyte skeleton separately for every region name.
    This index is built once per corpus and has no candidate or file-size cap.
    """
    def __init__(self, patterns):
        self.edges = [{}]
        self.failure = [0]
        self.outputs = [[]]
        for pattern, value in patterns:
            if not pattern:
                continue
            node = 0
            for byte in pattern:
                child = self.edges[node].get(byte)
                if child is None:
                    child = len(self.edges)
                    self.edges[node][byte] = child
                    self.edges.append({})
                    self.failure.append(0)
                    self.outputs.append([])
                node = child
            self.outputs[node].append(value)
        pending = deque(self.edges[0].values())
        while pending:
            node = pending.popleft()
            for byte, child in self.edges[node].items():
                failure = self.failure[node]
                while failure and byte not in self.edges[failure]:
                    failure = self.failure[failure]
                self.failure[child] = self.edges[failure].get(byte, 0)
                self.outputs[child].extend(self.outputs[self.failure[child]])
                pending.append(child)

    def find(self, data):
        edges, failure, outputs = self.edges, self.failure, self.outputs
        found = set()
        node = 0
        for byte in data:
            while node and byte not in edges[node]:
                node = failure[node]
            node = edges[node].get(byte, 0)
            if outputs[node]:
                found.update(outputs[node])
        return found


@dataclass
class AtlasMatch:
    atlas: dict
    regions: tuple
    pages: tuple
    weak_regions: tuple
    missing: tuple
    weight: float
    name_bonus: int
    proximity: int
    json_evidence: bool
    evidence_kind: str = "binary-string-heuristic"

    @property
    def hits(self):
        return len(self.regions) + len(self.pages)

    @property
    def plausible(self):
        return bool(self.hits or self.weak_regions)

    @property
    def key(self):
        # Known JSON requirements outrank incidental evidence. In binaries,
        # uncommon exact strings carry more weight than ubiquitous region names.
        complete = int(self.json_evidence and bool(self.regions) and not self.missing)
        return (complete, bool(self.regions), self.weight, len(self.regions),
                len(self.pages), self.name_bonus, self.proximity,
                len(self.weak_regions))

    def record(self):
        return {
            "atlas": str(self.atlas["path"]),
            "region_hits": len(self.regions), "page_hits": len(self.pages),
            "matched_regions": self.regions, "matched_pages": self.pages,
            "weak_region_hits": len(self.weak_regions),
            "weak_regions": self.weak_regions, "missing_regions": self.missing,
            "weighted_region_score": round(self.weight, 6),
            "name_bonus": self.name_bonus, "proximity": self.proximity,
            "evidence": self.evidence_kind,
            "atlas_text_sha256": self.atlas["_matching_text_sha256"],
        }


class AtlasMatcher:
    def __init__(self, atlases, name_bonus, distance):
        self.atlases = sorted(atlases, key=lambda a: path_key(a["path"]))
        for info in self.atlases:
            info["_matching_text_sha256"] = hashlib.sha256(info.get("text", "").encode("utf-8")).hexdigest()
        self.name_bonus = name_bonus
        # Path.resolve can touch the filesystem; never repeat it per pair when
        # thousands of assets occupy the same directories.
        self.distance = lru_cache(maxsize=16384)(distance)
        self.region_sets = [set(a.get("regions") or []) - {""} for a in self.atlases]
        self.page_sets = [set(Path(p["name"]).stem for p in a.get("pages", [])) - {""}
                          for a in self.atlases]
        frequency = Counter(r for regions in self.region_sets for r in regions)
        self.weights = {r: 1.0 + math.log((1 + len(self.atlases)) / (1 + n))
                        for r, n in frequency.items()}
        vocabulary = set().union(*self.region_sets, *self.page_sets)
        encoded = {}
        for word in vocabulary:
            raw = word.encode("utf-8")
            encoded[varint(len(raw) + 1) + raw] = word
        # Prefix included in the search pattern prevents body matching body_2.
        self.exact_index = BytePatternIndex(encoded.items())
        self.weak_index = BytePatternIndex((r.encode("utf-8").lower(), r) for r in self.weights)

    def rank(self, sk):
        is_json = sk.kind == "json" and isinstance(sk.json_obj, dict)
        required = attachment_paths(sk.json_obj) if is_json else set()
        if is_json:
            exact = required
            weak = set()
            evidence_kind = "json-attachments"
        else:
            data = sk.bytes if sk.kind == "binary" else sk.text.encode("utf-8")
            shared = shared_strings_38(data)
            if shared is not None:
                exact = shared
                evidence_kind = "binary-3.8-shared-strings"
            else:
                exact = self.exact_index.find(data)
                evidence_kind = "binary-string-heuristic"
            # Retain old substring possibilities for review, but do not treat
            # them as strong evidence or let them satisfy the hit threshold.
            weak = self.weak_index.find(data.lower()) - exact
        ranked = []
        for info, regions, pages in zip(self.atlases, self.region_sets, self.page_sets):
            matched = regions & exact
            ranked.append(AtlasMatch(
                info, tuple(sorted(matched)), tuple(sorted(pages & exact)),
                tuple(sorted(regions & weak)), tuple(sorted(required - regions)),
                math.fsum(self.weights[r] for r in sorted(matched)),
                self.name_bonus(sk, info), -self.distance(sk.path.parent, info["path"].parent), is_json, evidence_kind,
            ))
        # Stable path order resolves display order only; all tied candidates survive.
        ranked.sort(key=lambda m: m.key, reverse=True)
        return ranked


def add_matching_arguments(parser):
    parser.add_argument("--atlas-candidates", choices=("plausible", "all", "best"), default="plausible",
                        help="Alternative selection policy: plausible (default) selects every exact/weak evidence candidate; all selects every pair; best selects only the provisional first choice. Immediate alternative exports require --atlas-alternatives export. Reports always include every atlas.")
    parser.add_argument("--match-report-only", action="store_true",
                        help="Scan and write the atlas matching report without exporting sets or moving textures.")
    parser.add_argument("--atlas-alternatives", choices=("deferred", "export"), default="deferred",
                        help="Keep all alternatives in the report for on-demand review (default), or export all selected alternatives immediately. No candidate cap.")
    parser.add_argument("--materialize-atlas-report", help="Build one atlas pairing recorded in this JSONL matching report.")
    parser.add_argument("--materialize-skeleton", help="Exact source skeleton path in --materialize-atlas-report.")
    parser.add_argument("--materialize-atlas", help="Exact atlas path in --materialize-atlas-report.")


def candidate_matches(ranked, args, embedded=False):
    primary = None
    if not embedded and ranked:
        eligible = [m for m in ranked if m.hits >= args.min_hits]
        primary = eligible[0] if eligible else (ranked[0] if args.aggressive_atlas else None)
    if args.atlas_candidates == "best":
        selected = [primary] if primary else []
    elif args.atlas_candidates == "all":
        selected = list(ranked)
    else:
        selected = [m for m in ranked if m.plausible or m is primary]
    return primary, selected


def safe_name(value):
    # Source metadata is untrusted and must never escape the output directory.
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')[:100]
    if not value:
        value = "spine"
    if value.split('.')[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        value = "_" + value
    return value


def pair_id(sk, atlas):
    identity = str(sk.path.resolve()) + "\0" + (str(atlas["path"].resolve()) if atlas else "embedded-or-unresolved")
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


EXPORT_OPTIONS = (
    "link_mode", "allow_reuse_textures", "dims_fallback", "prefer_nearby_textures",
    "prefer_consistent_texture_dir", "rewrite_pages_to_match_source", "explain_match",
    "dedupe_textures", "stage_dim_candidates", "stage_dim_candidates_limit",
)


def skeleton_digest(sk):
    return hashlib.sha256(sk.bytes if sk.kind == "binary" else sk.text.encode("utf-8")).hexdigest()


def materialize_selection(api, report_path, header, row, atlas_path, texture_cache=None):
    """Build any audited pair, including zero-hit candidates, without reassigning others."""
    candidate = next((m for m in row["candidates"] if m["atlas"] == str(atlas_path)), None)
    if candidate is None:
        raise ValueError("Atlas is not present in this skeleton's report")
    skeletons = api.gather_skeletons_by_magic(Path(row["skeleton"]))
    atlases = api.index_atlases_by_magic(Path(atlas_path))
    if len(skeletons) != 1 or len(atlases) != 1:
        raise ValueError("Original skeleton or atlas is missing or no longer recognized")
    sk, atlas = skeletons[0], atlases[0]
    if skeleton_digest(sk) != row["skeleton_sha256"]:
        raise ValueError("Source skeleton changed since the audit; run a new build/audit")
    if hashlib.sha256(atlas["text"].encode("utf-8")).hexdigest() != candidate["atlas_text_sha256"]:
        raise ValueError("Source atlas changed since the audit; run a new build/audit")
    root = Path(header["source_root"])
    if not root.is_dir():
        raise ValueError("Original texture source folder is missing")
    cache = texture_cache if texture_cache is not None else {}
    cache_key = str(root.resolve())
    if cache_key not in cache:
        cache[cache_key] = api.build_texture_lookup(root)
    out_root = Path(report_path).parent / "_atlas_candidates" / (safe_name(sk.path.stem) + "__" + pair_id(sk, atlas))
    options = {k: header["export_options"][k] for k in EXPORT_OPTIONS}
    dst = api.export_set(sk, atlas, cache[cache_key], out_root, move=False, **options)
    with (Path(report_path).parent / "atlas_materializations.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"skeleton": str(sk.path), "atlas": str(atlas_path), "output": str(dst)}) + "\n")
    return dst


def materialize_from_report(api, report_path, skeleton_path, atlas_path):
    with Path(report_path).open(encoding="utf-8") as report:
        header = json.loads(next(report))
        for line in report:
            row = json.loads(line)
            if row.get("type") == "skeleton" and row["skeleton"] == str(skeleton_path):
                return materialize_selection(api, report_path, header, row, atlas_path)
    raise ValueError("Skeleton path is not present in this report")


def run_matching_pipeline(api, skels, atlases, tex_lut, out_root, args, source_root):
    """Rank once per skeleton, stream the audit, and export independent pairs."""
    matcher = AtlasMatcher(atlases, api._atlas_name_bonus, api.path_distance)
    report_path = out_root / "atlas_match_report.jsonl"
    # A new report/run directory avoids mixing old candidate outputs with a new audit.
    report_path.parent.mkdir(parents=True, exist_ok=True)
    built = matched = failed = pair_count = ambiguous = 0
    with report_path.open("w", encoding="utf-8") as report:
        report.write(json.dumps({"type": "header", "schema": 1, "skeletons": len(skels),
                                 "atlases": len(atlases), "policy": args.atlas_candidates,
                                 "report_only": args.match_report_only,
                                 "alternatives": args.atlas_alternatives,
                                 "source_root": str(source_root),
                                 "export_options": {k: getattr(args, k) for k in EXPORT_OPTIONS},
                                 "min_hits": args.min_hits, "aggressive": args.aggressive_atlas}) + "\n")
        for index, sk in enumerate(sorted(skels, key=lambda s: path_key(s.path)), 1):
            ranked = matcher.rank(sk)
            embedded = bool(sk.embedded_atlas_text and sk.embedded_atlas_pages)
            primary, selected = candidate_matches(ranked, args, embedded)
            if embedded:
                status = "embedded"
            elif primary is None:
                status = "unresolved"
            elif primary.json_evidence and not primary.missing and len([m for m in ranked if m.json_evidence and m.regions and not m.missing]) == 1:
                status = "unique-complete-json"
            else:
                status = "provisional"
            if len(selected) > 1:
                ambiguous += 1
            print(f"[match {index}/{len(skels)}] {sk.path.name}: {status}; {len(selected)} atlas candidate(s)", flush=True)
            if args.top_n > 0 or args.explain_match:
                for match in ranked[:max(args.top_n, 5 if args.explain_match else 0)]:
                    print(f"  {match.atlas['path'].name}: exact={len(match.regions)} weak={len(match.weak_regions)} missing={len(match.missing)} weighted={match.weight:.3f}")
            outcomes = {}
            exports = []
            # Embedded atlas metadata is direct association, ahead of disk guesses.
            if embedded or primary is None:
                exports.append(None)
            if primary is not None:
                exports.append(primary)
            exports.extend(m for m in selected if m is not primary)
            for match in exports:
                info = match.atlas if match else None
                alternate = match is not None and match is not primary
                deferred = alternate and args.atlas_alternatives == "deferred"
                destination = out_root
                if alternate and not deferred and not args.match_report_only:
                    destination = out_root / "_atlas_candidates" / (safe_name(sk.path.stem) + "__" + pair_id(sk, info))
                key = str(info["path"]) if info else "embedded-or-unresolved"
                outcome = {"role": "alternative" if alternate else "primary", "output": None, "error": None,
                           "deferred": deferred}
                if not args.match_report_only and not deferred:
                    try:
                        dst = api.export_set(
                            sk, info, tex_lut, destination,
                            move=args.move, link_mode=args.link_mode,
                            allow_reuse_textures=args.allow_reuse_textures,
                            dims_fallback=args.dims_fallback,
                            prefer_nearby_textures=args.prefer_nearby_textures,
                            prefer_consistent_texture_dir=args.prefer_consistent_texture_dir,
                            rewrite_pages_to_match_source=args.rewrite_pages_to_match_source,
                            explain_match=args.explain_match, dedupe_textures=args.dedupe_textures,
                            stage_dim_candidates=args.stage_dim_candidates,
                            stage_dim_candidates_limit=args.stage_dim_candidates_limit,
                        )
                        outcome["output"] = str(dst)
                        pair_count += int(info is not None or embedded)
                        if not alternate:
                            built += 1
                            matched += int(primary is not None or embedded)
                    except Exception as exc:
                        failed += 1
                        outcome["error"] = str(exc)
                        print(f"[WARN] failed pair {sk.path.name} / {key}: {exc}")
                outcomes[key] = outcome
            selected_ids = {id(m) for m in selected}
            report.write(json.dumps({
                "type": "skeleton", "skeleton": str(sk.path), "status": status,
                "skeleton_sha256": skeleton_digest(sk),
                "primary_atlas": str(primary.atlas["path"]) if primary else None,
                "embedded_atlas": embedded, "selected_candidates": len(selected),
                "exports": outcomes,
                "candidates": [dict(m.record(), selected=id(m) in selected_ids) for m in ranked],
            }, ensure_ascii=False) + "\n")
            report.flush()
        report.write(json.dumps({"type": "summary", "built_skeletons": built, "atlas_pairs": pair_count,
                                 "ambiguous_skeletons": ambiguous, "failed_exports": failed}) + "\n")
    print(f"[done] built {built}/{len(skels)} skeletons; {pair_count} atlas pairs; {ambiguous} with alternatives; {failed} failed exports")
    print(f"[report] {report_path}")
    if failed:
        raise RuntimeError(f"{failed} pair export(s) failed; see {report_path}")
    return built, matched, len(skels)
