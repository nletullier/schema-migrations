#!/usr/bin/env python3
"""Write the "what's new" Markdown of a migration, from the base and new models plus the spec.

    python3 -I whats_new.py --base old.xml --model new.xml --spec changes.json \
        --out 20261108_AddInvoices.md --migration-id 20261108_AddInvoices --lang fr \
        [--up path/to/Up.sql] [--down path/to/Down.sql] [--diff-svg diff.svg] [--schema-svg schema.svg]

The structural part (tables, columns, indexes, constraints, relations) is generated. The narrative
part comes from the spec: `title`, `summary`, `notes.why`, `notes.compatibility`, `notes.deployment`,
`notes.deferred`, `data.description`. Sections whose text is missing are left out, so fill the spec
rather than editing the Markdown (it is regenerated each time the migration is prepared).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rdm  # noqa: E402
from apply_changes import ON_ACTION_SQL, diff_snapshots  # noqa: E402

TEXT = {
    "fr": {
        "title": "Nouveautés du schéma", "migration": "Migration", "date": "Date",
        "why": "Pourquoi", "glance": "En bref", "diagram": "Ce qui change",
        "diagram_alt": "Différences de schéma", "created": "Tables créées", "altered": "Tables modifiées",
        "dropped": "Tables supprimées", "relations": "Relations", "data": "Reprise des données",
        "compat": "Compatibilité", "deploy": "Déploiement", "deferred": "Suppressions et suites différées",
        "files": "Fichiers", "col": "Colonne", "type": "Type", "null": "Nul", "default": "Défaut",
        "role": "Rôle", "yes": "oui", "no": "non", "pk": "Clé primaire", "indexes": "Index",
        "checks": "Contraintes", "uks": "Clés uniques", "unique": "unique", "where": "où",
        "added_col": "Colonne ajoutée", "dropped_col": "Colonne supprimée", "altered_col": "Colonne modifiée", "renamed_col": "Colonne renommée",
        "added_ix": "Index ajouté", "dropped_ix": "Index supprimé", "added_ck": "Contrainte ajoutée",
        "dropped_ck": "Contrainte supprimée", "added_uk": "Clé unique ajoutée", "dropped_uk": "Clé unique supprimée",
        "rel_name": "Relation", "rel_from": "Depuis", "rel_to": "Vers", "on_delete": "ON DELETE",
        "state": "État", "added": "ajoutée", "removed": "supprimée", "none": "aucune action",
        "n_created": ("{n} table créée", "{n} tables créées"),
        "n_altered": ("{n} table modifiée", "{n} tables modifiées"),
        "n_dropped": ("{n} table supprimée", "{n} tables supprimées"),
        "n_refs_added": ("{n} relation ajoutée", "{n} relations ajoutées"),
        "n_refs_dropped": ("{n} relation supprimée", "{n} relations supprimées"),
        "check": "Contrôle", "up": "Script Up", "down": "Script Down",
        "model": "Modèle de données", "schema": "Schéma complet après migration", "was": "avant",
        "data_lost": "Les données de cette table sont perdues : le Down ne recrée que la structure.",
        "data_before": "Avant les changements de structure :",
        "data_after": "Après les ajouts, avant les suppressions :",
    },
    "en": {
        "title": "Schema changes", "migration": "Migration", "date": "Date",
        "why": "Why", "glance": "At a glance", "diagram": "What changes",
        "diagram_alt": "Schema differences", "created": "New tables", "altered": "Modified tables",
        "dropped": "Dropped tables", "relations": "Relations", "data": "Data migration",
        "compat": "Compatibility", "deploy": "Deployment", "deferred": "Deferred removals and follow-ups",
        "files": "Files", "col": "Column", "type": "Type", "null": "Null", "default": "Default",
        "role": "Purpose", "yes": "yes", "no": "no", "pk": "Primary key", "indexes": "Indexes",
        "checks": "Constraints", "uks": "Unique keys", "unique": "unique", "where": "where",
        "added_col": "Column added", "dropped_col": "Column dropped", "altered_col": "Column modified", "renamed_col": "Column renamed",
        "added_ix": "Index added", "dropped_ix": "Index dropped", "added_ck": "Constraint added",
        "dropped_ck": "Constraint dropped", "added_uk": "Unique key added", "dropped_uk": "Unique key dropped",
        "rel_name": "Relation", "rel_from": "From", "rel_to": "To", "on_delete": "ON DELETE",
        "state": "Status", "added": "added", "removed": "removed", "none": "no action",
        "n_created": ("{n} table created", "{n} tables created"),
        "n_altered": ("{n} table modified", "{n} tables modified"),
        "n_dropped": ("{n} table dropped", "{n} tables dropped"),
        "n_refs_added": ("{n} relation added", "{n} relations added"),
        "n_refs_dropped": ("{n} relation dropped", "{n} relations dropped"),
        "check": "Check", "up": "Up script", "down": "Down script",
        "model": "Data model", "schema": "Full schema after the migration", "was": "was",
        "data_lost": "This table's rows are lost: Down only recreates the structure.",
        "data_before": "Before the structure changes:",
        "data_after": "After the additions, before the removals:",
    },
}


def cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ").strip()


def code(s: str) -> str:
    return f"`{s}`" if s else ""


def column_table(t: rdm.Table, cols, T) -> list[str]:
    out = [f"| {T['col']} | {T['type']} | {T['null']} | {T['default']} | {T['role']} |", "|---|---|---|---|---|"]
    for c in cols:
        role = c.description or ""
        if c.check:
            role = (role + " " if role else "") + f"{T['check']} : `{c.check}`".replace(" : ", ": " if T is TEXT["en"] else " : ")
        out.append(f"| {code(c.name)} | {cell(code(c.type))} | {T['yes'] if c.nullable else T['no']} | "
                   f"{cell(code(c.default))} | {cell(role)} |")
    return out


def index_line(t: rdm.Table, ix, T) -> str:
    cols = ", ".join(f"{t.col(cid).name}{'' if asc else ' DESC'}" for cid, asc in ix["columns"])
    s = f"{code(ix['name'])} ({cols})"
    if ix["unique"]:
        s += f", {T['unique']}"
    if ix.get("where"):
        s += f", {T['where']} `{ix['where']}`"
    return s


def rel_row(r: rdm.Reference, snap, state, T) -> str:
    pk = snap["by_id"][r.pk_table]
    fk = snap["by_id"][r.fk_table]
    src = ", ".join(f"{fk.name}.{fk.col(f).name}" for _, f in r.pairs)
    dst = ", ".join(f"{pk.name}.{pk.col(p).name}" for p, _ in r.pairs)
    act = ON_ACTION_SQL.get(r.on_delete, T["none"])
    return f"| {code(r.name)} | {code(src)} | {code(dst)} | {act} | {state} |"


def build(base: rdm.Model, new: rdm.Model, spec: dict, migration_id: str, lang: str, links: dict) -> str:
    T = TEXT.get(lang, TEXT["en"])
    old_s, new_s = rdm.schema_snapshot(base), rdm.schema_snapshot(new)
    d = diff_snapshots(old_s, new_s)
    notes = spec.get("notes") or {}
    out: list[str] = []
    title = spec.get("title") or spec.get("name") or migration_id
    out.append(f"# {title}")
    out.append("")
    out.append(f"**{T['migration']}** : `{migration_id}` · **{T['date']}** : {dt.date.today().isoformat()}"
               if lang == "fr" else
               f"**{T['migration']}**: `{migration_id}` · **{T['date']}**: {dt.date.today().isoformat()}")
    out.append("")
    if spec.get("summary"):
        out += [spec["summary"].strip(), ""]
    if notes.get("why"):
        out += [f"## {T['why']}", "", notes["why"].strip(), ""]

    glance = []
    for key, n in (("n_created", len(d["created"])), ("n_altered", len(d["altered"])), ("n_dropped", len(d["dropped"])),
                   ("n_refs_added", len(d["added_references"])), ("n_refs_dropped", len(d["dropped_references"]))):
        if n:
            glance.append("- " + T[key][0 if n == 1 else 1].format(n=n))
    if glance:
        out += [f"## {T['glance']}", ""] + glance + [""]
    if links.get("diff_svg"):
        out += [f"## {T['diagram']}", "", f"![{T['diagram_alt']}]({links['diff_svg']})", ""]

    if d["created"]:
        out += [f"## {T['created']}", ""]
        for tn in d["created"]:
            t = new_s["tables"][tn]
            out += [f"### `{tn}`", ""]
            if t.description:
                out += [t.description, ""]
            out += column_table(t, t.columns, T) + [""]
            extra = []
            if t.pk:
                extra.append(f"- {T['pk']} : " + ", ".join(code(t.col(c).name) for c in t.pk))
            for ix in t.indexes:
                extra.append(f"- {T['indexes']} : " + index_line(t, ix, T))
            for ak in t.alternate_keys:
                extra.append(f"- {T['uks']} : {code(ak['name'])} (" + ", ".join(t.col(c).name for c in ak["columns"]) + ")")
            for ck in t.checks:
                extra.append(f"- {T['checks']} : {code(ck['name'])} `{ck['expression']}`"
                             + (f" ({ck['description']})" if ck.get("description") else ""))
            if lang != "fr":
                extra = [e.replace(" : ", ": ", 1) for e in extra]
            out += extra + ([""] if extra else [])

    if d["altered"]:
        out += [f"## {T['altered']}", ""]
        sep = " : " if lang == "fr" else ": "
        for tn, ch in d["altered"].items():
            t_new, t_old = new_s["tables"][tn], old_s["tables"][tn]
            out += [f"### `{tn}`", ""]
            for c in ch["added_columns"]:
                out.append(f"- {T['added_col']}{sep}{code(c.name)} `{c.type}`, "
                           f"{'NULL' if c.nullable else 'NOT NULL'}"
                           + (f", DEFAULT `{c.default}`" if c.default else "")
                           + (f" — {c.description}" if c.description else ""))
            for o, n in ch["renamed_columns"]:
                out.append(f"- {T['renamed_col']}{sep}{code(o.name)} → {code(n.name)}")
            for o, n in ch["altered_columns"]:
                parts = []
                if o.type != n.type:
                    parts.append(f"`{o.type}` → `{n.type}`")
                if o.nullable != n.nullable:
                    parts.append(f"{'NULL' if o.nullable else 'NOT NULL'} → {'NULL' if n.nullable else 'NOT NULL'}")
                if o.default != n.default:
                    parts.append(f"DEFAULT `{o.default or '∅'}` → `{n.default or '∅'}`")
                if o.check != n.check:
                    parts.append(f"CHECK `{o.check or '∅'}` → `{n.check or '∅'}`")
                out.append(f"- {T['altered_col']}{sep}{code(n.name)}, " + ", ".join(parts))
            for c in ch["dropped_columns"]:
                out.append(f"- {T['dropped_col']}{sep}{code(c.name)} `{c.type}`")
            for ix in ch["added_indexes"]:
                out.append(f"- {T['added_ix']}{sep}" + index_line(t_new, ix, T))
            for ix in ch["dropped_indexes"]:
                out.append(f"- {T['dropped_ix']}{sep}" + index_line(t_old, ix, T))
            for ck in ch["added_checks"]:
                out.append(f"- {T['added_ck']}{sep}{code(ck['name'])} `{ck['expression']}`")
            for ck in ch["dropped_checks"]:
                out.append(f"- {T['dropped_ck']}{sep}{code(ck['name'])}")
            for ak in ch["added_uks"]:
                out.append(f"- {T['added_uk']}{sep}{code(ak['name'])}")
            for ak in ch["dropped_uks"]:
                out.append(f"- {T['dropped_uk']}{sep}{code(ak['name'])}")
            out.append("")

    if d["dropped"]:
        out += [f"## {T['dropped']}", ""]
        for tn in d["dropped"]:
            out.append(f"- `{tn}`. {T['data_lost']}")
        out.append("")

    if d["added_references"] or d["dropped_references"]:
        out += [f"## {T['relations']}", "",
                f"| {T['rel_name']} | {T['rel_from']} | {T['rel_to']} | {T['on_delete']} | {T['state']} |",
                "|---|---|---|---|---|"]
        out += [rel_row(r, new_s, T["added"], T) for r in d["added_references"]]
        out += [rel_row(r, old_s, T["removed"], T) for r in d["dropped_references"]]
        out.append("")

    data = spec.get("data") or {}
    if data.get("up") or data.get("before") or data.get("description"):
        out += [f"## {T['data']}", ""]
        if data.get("description"):
            out += [data["description"].strip(), ""]
        if data.get("before"):
            out += [T["data_before"], "", "```sql", data["before"].strip(), "```", ""]
        if data.get("up"):
            if data.get("before"):
                out += [T["data_after"], ""]
            out += ["```sql", data["up"].strip(), "```", ""]

    for key, label in (("compatibility", "compat"), ("deployment", "deploy"), ("deferred", "deferred")):
        if notes.get(key):
            out += [f"## {T[label]}", "", notes[key].strip(), ""]

    files = [(T["up"], links.get("up")), (T["down"], links.get("down")), (T["model"], links.get("model")),
             (T["schema"], links.get("schema_svg"))]
    files = [(lbl, p) for lbl, p in files if p]
    if files:
        out += [f"## {T['files']}", ""] + [f"- {lbl} : [{Path(p).name}]({p})" if lang == "fr"
                                           else f"- {lbl}: [{Path(p).name}]({p})" for lbl, p in files] + [""]
    return "\n".join(out).rstrip() + "\n"


def rel(path: str | None, start: Path) -> str | None:
    if not path:
        return None
    return os.path.relpath(Path(path).resolve(), start.resolve())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--migration-id", required=True)
    ap.add_argument("--lang", default="en")
    for k in ("up", "down", "diff-svg", "schema-svg", "model-link"):
        ap.add_argument(f"--{k}")
    a = ap.parse_args(argv)
    out_dir = Path(a.out).parent
    links = {"up": rel(a.up, out_dir), "down": rel(a.down, out_dir), "diff_svg": rel(a.diff_svg, out_dir),
             "schema_svg": rel(a.schema_svg, out_dir), "model": rel(a.model_link, out_dir)}
    md = build(rdm.Model.load(a.base), rdm.Model.load(a.model),
               json.loads(Path(a.spec).read_text(encoding="utf-8")), a.migration_id, a.lang, links)
    Path(a.out).write_text(md, encoding="utf-8")
    print(f"what's new written to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
