---
name: schema-migrations
description: Prepare relational database schema migrations as a reviewable package, one folder per migration — Up/Down SQL, the Redgate Data Modeler XML model after the migration, a diagram of the whole schema with the changes coloured in (SVG, green added / red removed), a clean full-schema SVG and a "what's new" Markdown note — then stop for the user's review before any code depends on it. Use this skill whenever a task needs a new table, column, index, constraint, foreign key or data migration (even inside a bigger feature), whenever the user mentions migrations, schema changes, the data model, an ERD/diagram of the database, Redgate / Vertabelo / Data Modeler, or a project contains `.schema-migrations.json`. Use it too to start a model from scratch for a project that has none.
allowed-tools: Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/*)
---

# Schema migrations

A schema change is the riskiest and least reversible part of most features. This skill turns it into a
small package the user can review like a pull request, *before* anything is applied or coded against it.
Each migration is a folder, `<migrations dir>/<timestamp>_<Name>/`, holding everything about it:

| File | Role |
|---|---|
| `changes.json` | The spec you write. Everything else is generated from it and the previous model. |
| Up / Down SQL | The migration scripts (names set by the config, e.g. `<id>.Up.sql`). |
| `model.xml` | The Redgate Data Modeler model after this migration (tables, columns, references, layout). |
| `diff.svg` | The whole schema with the changes coloured in: green added, red removed (struck through), amber modified. |
| `schema.svg` | The whole schema after the migration, without colours. |
| `<id>.md` | A "what's new" note in the project's language: why, tables, relations, data migration, deployment. |

The current model of the project is the `model.xml` of its latest migration folder; the first prepared
migration starts from a *baseline* model (an export from Redgate, or an empty model). Folders of older
migrations made before the skill was adopted simply have no `model.xml`.

The scripts are plain Python 3 (standard library only) in `scripts/`. Run them with `python3 -I`.

## The workflow

### 0. Set up (once per project)

Look for `.schema-migrations.json` (or `.claude/schema-migrations.json`) at the project root. If there is
none, ask the user where migrations live, how they are created in this project, whether a Redgate export
of the current schema exists (it becomes the baseline), and which language the notes should use; then run

```bash
python3 -I ${CLAUDE_SKILL_DIR}/scripts/prepare_migration.py init --dir db/migrations [--baseline path/to/export.xml]
```

and edit the generated config. `init` starts an empty baseline when there is no model at all. Config keys:

```json
{
  "language": "fr",                               // "fr" or "en" for the what's new note
  "dialect": "postgresql",
  "baseline": "db/migrations/baseline.xml",       // model the first prepared migration starts from
  "migrations": {
    "dir": "db/migrations",                       // holds one folder per migration
    "create": "./add-migration.sh {name}",        // optional command that creates the migration folder
    "up": "{id}.Up.sql",                          // file names inside the folder; {id} = <timestamp>_<Name>
    "down": "{id}.Down.sql"
  }
}
```

Without `create`, the script creates the folder `<dir>/<yyyyMMddHHmmss>_<Name>/` and its two empty SQL
files itself. With `create`, the command must leave that folder with the Up/Down files in it. If the
project's migrations are code (EF Core, Alembic, Knex…), make `create` run the tool's own command and
move its output into the folder, and make the migration load its `.sql` files, so the SQL stays the
single source. For .NET with EF Core, follow `${CLAUDE_SKILL_DIR}/../../dotnet/README.md` and copy its
`template/` (EF Core as a plain runner of the SQL files, one folder per migration).

### 1. Gather every schema change of the piece of work first

Read the code and the model, then list **all** the schema changes the whole feature will need, across all
its steps, and propose them in one go. Migrations are cheap to write and expensive to live with: each one
is a deployment step, a rollback path and a line in the history. Ask the user about genuine design choices
(nullability, delete behaviour, enumerations, what "current" means…) before writing the spec.

### 2. Create the migration and write the spec

```bash
python3 -I ${CLAUDE_SKILL_DIR}/scripts/prepare_migration.py new AddInvoices
```

This runs the project's create command (or creates the folder and files) and writes a `changes.json`
skeleton in the folder. Fill the spec — the format is in
`references/spec-format.md`, with complete examples in `references/examples/`. Things that make the
review worthwhile:

- a `description` on every new table, column and reference, in the project's language: it lands in the
  model, the diagram tooltip and the what's new note, so it is the documentation;
- `notes.why`, `notes.compatibility`, `notes.deployment` and `notes.deferred` written for the person who
  will deploy, not for yourself;
- `near` on each new table, naming the table it relates to most, so the diagram places it nearby;
- data migrations in `data.up` that are **idempotent** (re-runnable without duplicating anything) and a
  `data.description` saying what they do in plain words, with real counts once you have them (step 4).

### 3. Build

```bash
python3 -I ${CLAUDE_SKILL_DIR}/scripts/prepare_migration.py build AddInvoices
```

`build` always restarts from the previous migration's `model.xml` (or the baseline), so edit the spec and
build again as often as needed. It overwrites the Up/Down files, `model.xml` and the review files: never
hand-edit them, change the spec. It refuses to rebuild a migration that a later one already builds on
(`--force` overrides, then rebuild the later ones in order). Read the warnings it prints (for instance a
NOT NULL column added without default).

### 4. Check it yourself before showing it

- **Look at the diagrams.** `diff.svg` is the size of the whole schema: rasterise it, view it scaled down,
  then crop around the changes. For instance with headless Chrome:
  `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --screenshot=out.png --window-size=W,H file:///abs/path/diff.svg`
  (W×H from the SVG's width/height), or `qlmanage -t -s 2000 -o . diff.svg` on macOS, or
  `rsvg-convert` on Linux. Check that new tables sit near their relatives, lines do not cross tables,
  and text fits. If the placement is poor, change `near` and rebuild.
- **Dry-run the SQL** when a development database is reachable: run Up, your checks, then Down, all inside
  `BEGIN; … ROLLBACK;`. Check the data migration's counts and edge cases (junk values, duplicates, time
  zones) and put the real numbers in `data.description`. Never run anything against production.
- **Read the what's new note** as the reviewer will.

### 5. Hand over for review — and stop

Tell the user which folder to look at (the what's new note first; it embeds `diff.svg`), summarise the
choices that deserve a second look, and **stop there**: do not apply the migration, and do not write code
that depends on it, until the user approves. Iterate on their remarks through the spec and `build`.

### 6. After approval

1. Apply the migration with the project's own command (on the development database).
2. Write the code that uses the new schema. The whole migration folder is committed with the feature.

## Rules that keep migrations safe

- **Never modify a migration that has been applied anywhere** (shared database, staging, production):
  write a new one. While it is still under review and only on your machine, rebuilding it is fine.
- **One migration under review at a time.** A second one prepared on top of a migration still in review
  starts from the first one's `model.xml`; changing the first means `build --force` on it, then `build`
  on the second.
- **Destructive changes go in two steps** when old code may still be running (rolling deploys, mobile
  apps that update late): first stop using the column or table and ship, then drop it in a later
  migration. Say so in `notes.deferred`.
- **Additive first.** A new NOT NULL column needs a default or a data step; a narrowing type change needs
  existing data to fit. The generated SQL comments flag these, but you decide.
- **Data migrations**: `data.before` runs before any structural change; `data.up` runs after the
  additions and before the removals, so it can read a column the migration drops and write a table it
  creates. Give the reverse in `data.down` / `data.beforeDown` when it exists.
- **Renames** are matched by model id, so `alterColumns[].rename` generates `RENAME COLUMN`, not a drop and
  an add. Tables cannot be renamed through the spec: create, copy, drop in two migrations.
- If the project keeps another schema reference (a hand-maintained DDL file, docs), update it in the
  same change.

## Scripts

| Script | Use |
|---|---|
| `prepare_migration.py init/new/build` | The whole workflow above, driven by the project config. |
| `apply_changes.py --model base.xml --spec changes.json --out new.xml --up up.sql --down down.sql` | Apply a spec without a project config. `--init --out model.xml` writes an empty model. |
| `render_svg.py` | Render a model (or a diff between two models) to SVG on its own; see `--help`. |
| `whats_new.py` | Write the note on its own; see `--help`. |
| `rdm.py` | Library: load/save the Redgate XML byte for byte, ids, layout helpers. |

`references/redgate-xml-format.md` describes the XML format, for when something in a model needs a fix
the spec cannot express (moving a table display, a sequence, a view). Edit the model with `rdm.py` rather
than by regex, and re-save it to keep Redgate's formatting.

## Limits

- PostgreSQL is the dialect the SQL generator is written and tested for; other engines would need their own
  type and constraint syntax.
- Views, sequences, functions, triggers and grants are not modelled by the spec: put them in `data.up`
  (raw SQL is fine) and describe them in the notes.
- The layout is a heuristic: tables go to the nearest free spot that covers the fewest existing lines,
  lines are orthogonal and avoid tables when they can. It is a readable starting point, not Redgate's
  own router; the user can still open the XML in Redgate and move things.
