#!/usr/bin/env python3
"""Drive the whole preparation of a schema migration from the project's configuration.

    python3 -I prepare_migration.py init                  # write a starter .schema-migrations.json
    python3 -I prepare_migration.py new AddInvoices    # create the migration folder, its SQL files and a spec
    python3 -I prepare_migration.py build AddInvoices  # spec -> model, Up/Down SQL, diff.svg, schema.svg, note

Every migration lives in its own folder, `<migrations.dir>/<timestamp>_<Name>/`, which holds everything
about it: the Up/Down SQL, the spec (changes.json), the data model after the migration (model.xml), the
diagrams (diff.svg, schema.svg) and the what's new note (<id>.md).

The model a migration starts from is the model.xml of the latest earlier migration folder, or the
project's baseline model when there is none. `build` can therefore be re-run as often as the review needs.
It refuses to rebuild a migration that later migrations already build on, unless --force is given.

Run from anywhere inside the project: the configuration is looked up from the current directory upwards.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rdm  # noqa: E402
import apply_changes  # noqa: E402
import render_svg  # noqa: E402
import whats_new  # noqa: E402

CONFIG_NAMES = (".schema-migrations.json", ".claude/schema-migrations.json")
CONFIG_NAME = CONFIG_NAMES[0]
MIGRATION_DIR_RE = re.compile(r"^\d{8,}_[A-Za-z0-9_]+$")

DEFAULT_CONFIG = {
    "language": "en",
    "dialect": "postgresql",
    "baseline": "migrations/baseline.xml",
    "migrations": {
        "dir": "migrations",
        "create": None,
        "up": "{id}.up.sql",
        "down": "{id}.down.sql",
    },
}

SPEC_SKELETON = {
    "name": "",
    "title": "",
    "summary": "",
    "notes": {"why": "", "compatibility": "", "deployment": "", "deferred": ""},
    "createTables": [],
    "alterTables": [],
    "dropTables": [],
    "addReferences": [],
    "dropReferences": [],
    "data": {"description": "", "before": "", "up": "", "down": "", "beforeDown": ""},
}

SPEC, MODEL, DIFF, SCHEMA = "changes.json", "model.xml", "diff.svg", "schema.svg"


# --------------------------------------------------------------------------- configuration

def find_root() -> Path:
    p = Path.cwd().resolve()
    for d in [p, *p.parents]:
        if any((d / n).exists() for n in CONFIG_NAMES):
            return d
    raise SystemExit(f"{' or '.join(CONFIG_NAMES)} not found from {p} upwards: "
                     "run `prepare_migration.py init` at the project root")


def load_config(root: Path) -> dict:
    path = next(root / n for n in CONFIG_NAMES if (root / n).exists())
    cfg = json.loads(path.read_text(encoding="utf-8"))
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update({k: v for k, v in cfg.items() if k != "migrations"})
    merged["migrations"].update(cfg.get("migrations") or {})
    return merged


# --------------------------------------------------------------------------- migration folders

class Migration:
    def __init__(self, root: Path, cfg: dict, folder: Path):
        self.root, self.cfg, self.folder = root, cfg, folder
        self.id = folder.name
        mig = cfg["migrations"]
        self.up = folder / mig["up"].replace("{id}", self.id)
        self.down = folder / mig["down"].replace("{id}", self.id)
        self.spec = folder / SPEC
        self.model = folder / MODEL
        self.diff = folder / DIFF
        self.schema = folder / SCHEMA
        self.note = folder / f"{self.id}.md"


def migrations_dir(root: Path, cfg: dict) -> Path:
    return root / cfg["migrations"]["dir"]


def all_folders(root: Path, cfg: dict) -> list[Path]:
    d = migrations_dir(root, cfg)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_dir() and MIGRATION_DIR_RE.match(p.name))


def find_migration(root: Path, cfg: dict, name: str) -> Migration | None:
    hits = [p for p in all_folders(root, cfg) if p.name.endswith(f"_{name}")
            and p.name[: -len(name) - 1].isdigit()]
    return Migration(root, cfg, hits[-1]) if hits else None


def base_model_path(root: Path, cfg: dict, mig: Migration) -> Path | None:
    earlier = [p for p in all_folders(root, cfg) if p.name < mig.id and (p / MODEL).exists()]
    if earlier:
        return earlier[-1] / MODEL
    baseline = root / cfg["baseline"]
    return baseline if baseline.exists() else None


def later_with_model(root: Path, cfg: dict, mig: Migration) -> list[str]:
    return [p.name for p in all_folders(root, cfg) if p.name > mig.id and (p / MODEL).exists()]


# --------------------------------------------------------------------------- commands

def cmd_init(args) -> int:
    root = Path.cwd().resolve()
    target = root / CONFIG_NAME
    if target.exists():
        print(f"{target} already exists")
    else:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        if args.dir:
            cfg["migrations"]["dir"] = args.dir
            cfg["baseline"] = f"{args.dir.rstrip('/')}/baseline.xml"
        if args.baseline:
            cfg["baseline"] = args.baseline
        target.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {target}: adjust language, baseline and migrations before going further")
    cfg = load_config(root)
    baseline = root / cfg["baseline"]
    if not baseline.exists() and not any((p / MODEL).exists() for p in all_folders(root, cfg)):
        baseline.parent.mkdir(parents=True, exist_ok=True)
        rdm.Model.empty(root.name).save(baseline)
        print(f"no model found: started an empty baseline at {baseline}")
    return 0


def cmd_new(args) -> int:
    root = find_root()
    cfg = load_config(root)
    name = args.name
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
        print("the migration name must be an identifier (letters, digits, underscores)", file=sys.stderr)
        return 1
    mig = find_migration(root, cfg, name)
    if mig is None:
        create = cfg["migrations"].get("create")
        if create:
            cmd = create.replace("{name}", shlex.quote(name))
            print(f"$ {cmd}")
            r = subprocess.run(cmd, shell=True, cwd=root)
            if r.returncode != 0:
                print("migration creation command failed", file=sys.stderr)
                return r.returncode
        else:
            ts = dt.datetime.now().strftime("%Y%m%d%H%M%S")
            folder = migrations_dir(root, cfg) / f"{ts}_{name}"
            folder.mkdir(parents=True, exist_ok=True)
            m = Migration(root, cfg, folder)
            m.up.write_text("", encoding="utf-8")
            m.down.write_text("", encoding="utf-8")
        mig = find_migration(root, cfg, name)
        if mig is None or not mig.up.exists():
            print(f"no folder <timestamp>_{name} with its Up/Down files in {migrations_dir(root, cfg)} after creation: "
                  "the create command must make it (see migrations.dir/up/down in the config)", file=sys.stderr)
            return 1
    if not mig.spec.exists():
        sk = json.loads(json.dumps(SPEC_SKELETON))
        sk["name"] = name
        mig.spec.write_text(json.dumps(sk, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    base = base_model_path(root, cfg, mig)
    print(json.dumps({"id": mig.id, "folder": str(mig.folder), "spec": str(mig.spec),
                      "base": str(base) if base else "(empty model)"}, indent=2))
    return 0


def cmd_build(args) -> int:
    root = find_root()
    cfg = load_config(root)
    mig = find_migration(root, cfg, args.name)
    if mig is None:
        print(f"no migration folder for {args.name}: run `new {args.name}` first", file=sys.stderr)
        return 1
    later = later_with_model(root, cfg, mig)
    if later and not args.force:
        print(f"{', '.join(later)} already build on {mig.id}. Rebuilding it would change the model they start "
              f"from (and it may be applied already). Use --force, then build them again in order.", file=sys.stderr)
        return 1
    base_path = base_model_path(root, cfg, mig)
    base = rdm.Model.load(base_path) if base_path else rdm.Model.empty(root.name)
    spec = json.loads(mig.spec.read_text(encoding="utf-8"))
    try:
        new, warnings = apply_changes.apply_spec(base, spec)
    except apply_changes.SpecError as e:
        print(f"spec error: {e}", file=sys.stderr)
        return 2
    new.save(mig.model)
    up_sql, down_sql = apply_changes.generate_sql(base, new, spec, str(mig.spec.relative_to(root)))
    mig.up.write_text(up_sql, encoding="utf-8")
    mig.down.write_text(down_sql, encoding="utf-8")
    new = rdm.Model.load(mig.model)
    title = spec.get("title") or args.name
    mig.diff.write_text(render_svg.Renderer(new, base, True, f"{mig.id} — {title}").render(), encoding="utf-8")
    mig.schema.write_text(render_svg.Renderer(new, None, False, mig.id).render(), encoding="utf-8")
    links = {k: whats_new.rel(str(v), mig.folder) for k, v in
             (("up", mig.up), ("down", mig.down), ("diff_svg", mig.diff), ("schema_svg", mig.schema),
              ("model", mig.model))}
    mig.note.write_text(whats_new.build(base, new, spec, mig.id, cfg.get("language", "en"), links), encoding="utf-8")
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    if later:
        print(f"warning: build again, in order: {', '.join(later)}", file=sys.stderr)
    print(json.dumps({"id": mig.id, "base": str(base_path) if base_path else "(empty model)",
                      "folder": str(mig.folder),
                      "files": [p.name for p in (mig.up, mig.down, mig.model, mig.diff, mig.schema, mig.note)]},
                     indent=2))
    print("\nReview these files with the user before applying the migration or writing code that depends on it.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--dir", help="folder that holds the migration folders")
    p.add_argument("--baseline", help="model to start from when no migration has one yet")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("new")
    p.add_argument("name")
    p.set_defaults(func=cmd_new)
    p = sub.add_parser("build")
    p.add_argument("name")
    p.add_argument("--force", action="store_true", help="rebuild even if later migrations build on this one")
    p.set_defaults(func=cmd_build)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
