from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

GCODE_KEYS = ("machine_start_gcode", "machine_end_gcode", "change_filament_gcode",
              "time_lapse_gcode", "layer_change_gcode", "wrapping_detection_gcode")
TEMPLATE_IDENTITY_KEYS = ("name", "type", "from", "instantiation", "inherits", "setting_id")
IDENTIFIER_SOURCES = ("src/libslic3r/PrintConfig.cpp", "src/libslic3r/GCode.cpp",
                      "src/libslic3r/GCode/*.cpp", "src/libslic3r/PlaceholderParser.cpp",
                      "src/libslic3r/Print.cpp")
DATE_RE = re.compile(r";=+ date: (\d{8})")
MACHINE_PREFIX = "resources/profiles/BBL/machine/"
BBL_JSON_REL = "resources/profiles/BBL.json"
BAMBU_ARCHIVE_URL = "https://github.com/bambulab/BambuStudio/archive/refs/tags/{tag}.tar.gz"


class SyncError(Exception):
    pass


def download_archive(tag, dest):
    url = BAMBU_ARCHIVE_URL.format(tag=tag)
    target = dest / f"{tag}.tar.gz"
    with urllib.request.urlopen(url) as response:
        data = response.read()
    target.write_bytes(data)
    return target


def read_tag(archive):
    result = {}
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split("/", 1)
            if len(parts) != 2:
                continue
            rel = parts[1]
            is_bbl_json = rel == BBL_JSON_REL
            is_machine = (rel.startswith(MACHINE_PREFIX) and rel.endswith(".json")
                          and "/" not in rel[len(MACHINE_PREFIX):])
            is_hms = rel.startswith("resources/hms/")
            is_printer = rel.startswith("resources/printers/")
            if not (is_bbl_json or is_machine or is_hms or is_printer):
                continue
            fileobj = tf.extractfile(member)
            if fileobj is None:
                continue
            result[rel] = fileobj.read()
    return result


def load_doc(data):
    return json.loads(data.decode("utf-8"))


def dump_doc(doc):
    return json.dumps(doc, indent="\t", ensure_ascii=False) + "\n"


def write_if_changed(path, doc, dry_run):
    if path.exists() and load_doc(path.read_bytes()) == doc:
        return False
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_doc(doc), encoding="utf-8", newline="\n")
    return True


def copy_if_changed(path, data, dry_run):
    if path.exists() and path.read_bytes() == data:
        return False
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return True


def index_templates(machines):
    return {doc["name"]: doc for doc in machines.values()}


def resolve_includes(doc, by_name, source):
    own = {k: v for k, v in doc.items() if k != "include"}
    includes = doc.get("include", [])
    if isinstance(includes, str):
        includes = [includes]
    applied = {}
    for name in includes:
        tpl = by_name.get(name)
        if tpl is None:
            raise SyncError(f"{source}: include not found: {name}")
        for key, value in tpl.items():
            if key in TEMPLATE_IDENTITY_KEYS:
                continue
            if key not in GCODE_KEYS:
                raise SyncError(f"{source}: template {name} carries non-gcode key {key}")
            if value == "":
                continue
            applied[key] = value
    result = dict(own)
    for key, value in applied.items():
        if key not in own:
            result[key] = value
    return result


def merge_gcode(ours, flat):
    merged = dict(ours)
    changed = []
    for key in GCODE_KEYS:
        if key not in flat:
            continue
        new_value = flat[key]
        if key in merged:
            if merged[key] != new_value:
                changed.append(key)
            merged[key] = new_value
        else:
            merged[key] = new_value
            changed.append(key)
    return merged, changed


def merge_index(ours, theirs, template_names, skipped=frozenset()):
    merged = dict(ours)
    merged["version"] = theirs["version"]
    added = []
    for list_key in ("machine_model_list", "machine_list"):
        merged_list = list(merged.get(list_key, []))
        existing_names = {entry["name"] for entry in merged_list}
        for entry in theirs.get(list_key, []):
            name = entry["name"]
            if name in existing_names or name in template_names or name in skipped:
                continue
            merged_list.append(entry)
            existing_names.add(name)
            added.append(name)
        merged[list_key] = merged_list
    return merged, added


def known_option_keys(repo):
    text = (repo / "src/libslic3r/PrintConfig.cpp").read_text(encoding="utf-8")
    keys = set(re.findall(r'this->add(?:_nullable)?\("([a-z0-9_]+)"', text))
    keys.update(re.findall(r'opt_key == "([a-z0-9_]+)"', text))
    for prefix in re.findall(r'this->add\("([a-z0-9_]+_)" \+ axis\.name', text):
        keys.update(prefix + axis for axis in ("x", "y", "z", "e"))
    preset_text = (repo / "src/libslic3r/Preset.hpp").read_text(encoding="utf-8")
    keys.update(re.findall(r'#define BBL_JSON_KEY_\w+\s+"([a-z0-9_]+)"', preset_text))
    return keys


def known_identifiers(repo):
    ids = set()
    for pattern in IDENTIFIER_SOURCES:
        for path in sorted(repo.glob(pattern)):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            ids.update(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', text))
    return ids


def parser_keywords(repo):
    path = repo / "src/libslic3r/PlaceholderParser.cpp"
    text = path.read_text(encoding="utf-8")
    match = re.search(r"keywords\.add(.*?);", text, re.S)
    if not match:
        return set()
    return set(re.findall(r'\("([a-z_]+)"\)', match.group(1)))


def extract_identifiers(gcode, keywords):
    locals_declared = set()
    brace_ids = set()
    spans = []
    for match in re.finditer(r"\{([^{}]*)\}", gcode):
        spans.append((match.start(), match.end()))
        content = re.sub(r'"[^"]*"', "", match.group(1))
        locals_declared.update(re.findall(r"\b(?:local|global)\s+([A-Za-z_]\w*)", content))
        brace_ids.update(re.findall(r"(?<![\w.])[A-Za-z_]\w*", content))
    outside = []
    last = 0
    for start, end in spans:
        outside.append(gcode[last:start])
        last = end
    outside.append(gcode[last:])
    legacy_ids = set(re.findall(r"\[([A-Za-z_]\w*)\]", "".join(outside)))
    return (brace_ids | legacy_ids) - set(keywords) - locals_declared - {"true", "false"}


def validate(new_docs, gcode, repo, added=None):
    option_keys = known_option_keys(repo)
    allowed = option_keys | known_identifiers(repo)
    keywords = parser_keywords(repo)
    offenders = []
    for file, doc in new_docs.items():
        if added is None or file in added:
            for key in doc:
                if key != "include" and key not in option_keys:
                    offenders.append(f"{file}: unknown key {key}")
        if "include" in doc:
            offenders.append(f"{file}: include key left after flattening")
    for ref, text in gcode.items():
        for name in extract_identifiers(text, keywords):
            if name not in allowed:
                offenders.append(f"{ref}: unknown placeholder {name}")
    return sorted(offenders)


def gcode_date(text):
    match = DATE_RE.search(text)
    return match.group(1) if match else None


def printers_newer_locally(files, repo):
    theirs = files.get("resources/printers/version.txt")
    local_path = repo / "resources/printers/version.txt"
    if theirs is None or not local_path.exists():
        return None
    local_version = local_path.read_text(encoding="utf-8").strip()
    tag_version = theirs.decode("utf-8").strip()
    if local_version > tag_version:
        return local_version, tag_version
    return None


def _collect_machine_docs(files):
    return {rel: load_doc(data) for rel, data in files.items() if rel.startswith(MACHINE_PREFIX)}


def _is_template(name, doc):
    return " template " in name and doc.get("instantiation") == "false"


def sync(tag, repo, archive, dry_run):
    files = read_tag(archive)
    machine_docs = _collect_machine_docs(files)
    by_name = index_templates(machine_docs)
    template_names = {name for name, doc in by_name.items() if _is_template(name, doc)}

    new_docs = {}
    added_machines = []
    gcode_changes = []
    gcode_texts = {}

    for rel, doc in machine_docs.items():
        name = doc.get("name", "")
        if name in template_names:
            continue
        filename = rel[len(MACHINE_PREFIX):]
        flat = resolve_includes(doc, by_name, filename)
        own_path = repo / MACHINE_PREFIX / filename
        if own_path.exists():
            ours = load_doc(own_path.read_bytes())
            final, changed = merge_gcode(ours, flat)
            if changed:
                old_date = gcode_date(ours.get("machine_start_gcode", ""))
                new_date = gcode_date(final.get("machine_start_gcode", ""))
                gcode_changes.append((filename, changed, old_date, new_date))
        else:
            final = flat
            added_machines.append(filename)
        new_docs[filename] = final
        for key in GCODE_KEYS:
            if key in final:
                gcode_texts[f"{filename}:{key}"] = final[key]

    skipped_files = set()
    for filename in added_machines:
        refs = {f"{filename}:{key}": gcode_texts[f"{filename}:{key}"] for key in GCODE_KEYS if f"{filename}:{key}" in gcode_texts}
        if validate({filename: new_docs[filename]}, refs, repo, {filename}):
            skipped_files.add(filename)
    skipped_models = {new_docs[f].get("name", "") for f in skipped_files if new_docs[f].get("type") == "machine_model"}
    for filename in added_machines:
        if new_docs[filename].get("printer_model") in skipped_models:
            skipped_files.add(filename)
    skipped_names = {new_docs[f].get("name", "") for f in skipped_files}
    for filename in skipped_files:
        new_docs.pop(filename)
        for key in GCODE_KEYS:
            gcode_texts.pop(f"{filename}:{key}", None)
    added_machines = [f for f in added_machines if f not in skipped_files]

    ours_bbl_path = repo / BBL_JSON_REL
    ours_bbl = load_doc(ours_bbl_path.read_bytes())
    theirs_bbl = load_doc(files[BBL_JSON_REL])
    merged_bbl, added_models = merge_index(ours_bbl, theirs_bbl, template_names, skipped_names)

    offenders = validate(new_docs, gcode_texts, repo, set(added_machines))
    if offenders:
        for offender in offenders:
            print(offender)
        return 1

    written = 0
    for filename, doc in new_docs.items():
        if write_if_changed(repo / MACHINE_PREFIX / filename, doc, dry_run):
            written += 1
    if write_if_changed(ours_bbl_path, merged_bbl, dry_run):
        written += 1

    hms_added = hms_updated = 0
    printers_added = printers_updated = 0
    newer = printers_newer_locally(files, repo)
    if newer:
        print(f"printers: kept local {newer[0]}, tag has older {newer[1]}")
    for rel, data in files.items():
        if rel.startswith("resources/hms/"):
            path = repo / rel
            existed = path.exists()
            if copy_if_changed(path, data, dry_run):
                written += 1
                if existed:
                    hms_updated += 1
                else:
                    hms_added += 1
        elif rel.startswith("resources/printers/") and not newer:
            path = repo / rel
            existed = path.exists()
            if copy_if_changed(path, data, dry_run):
                written += 1
                if existed:
                    printers_updated += 1
                else:
                    printers_added += 1

    print(f"archive: {archive}")
    for filename in sorted(skipped_files):
        print(f"machine skipped, needs unsupported options: {filename}")
    for filename in added_machines:
        print(f"machine added: {filename}")
    for filename, changed, old_date, new_date in gcode_changes:
        keys_str = ", ".join(changed)
        if old_date and new_date and old_date != new_date:
            print(f"machine gcode: {filename}: {keys_str} date {old_date}->{new_date}")
        else:
            print(f"machine gcode: {filename}: {keys_str}")
    for name in added_models:
        print(f"model added: {name}")
    print(f"BBL.json version: {ours_bbl.get('version')} -> {merged_bbl.get('version')}")
    print(f"hms: {hms_added} added, {hms_updated} updated")
    print(f"printers: {printers_added} added, {printers_updated} updated")
    if dry_run:
        print(f"files that would be written: {written}")
    else:
        print(f"files written: {written}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--archive")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args(argv)
    repo = Path(args.repo)
    try:
        if args.archive:
            return sync(args.tag, repo, Path(args.archive), args.dry_run)
        with tempfile.TemporaryDirectory() as tmp:
            archive = download_archive(args.tag, Path(tmp))
            return sync(args.tag, repo, archive, args.dry_run)
    except SyncError as exc:
        print(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
