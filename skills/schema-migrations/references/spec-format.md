# Spec format (`changes.json`)

One JSON object per migration. Every key is optional except `name`. Operations are applied to the base
model in this order: `dropReferences`, `dropTables`, `createTables`, `alterTables`, `addReferences`.
Complete examples, one per migration of a small library schema: `examples/01-create-tables.json`, `examples/02-add-table-and-migrate-data.json` (a new table filled from an existing column), `examples/03-drop-and-rename.json` (the deferred drop, and a rename).

```jsonc
{
  "name": "AddInvoices",                 // migration name, as given to `new`
  "title": "Invoices",                   // title of the what's new note and of diff.svg
  "summary": "…",                        // first paragraph of the note
  "notes": {                             // free Markdown, one section each in the note
    "why": "…",
    "compatibility": "…",                // what old code / old apps still see, bridges, endpoints
    "deployment": "…",                   // ordered steps for whoever deploys
    "deferred": "…"                      // removals left for a later migration
  },
  "createTables": [ … ],
  "alterTables":  [ … ],
  "dropTables":   ["old_table"],         // also drops its references and displays
  "addReferences":  [ … ],
  "dropReferences": ["fk_name"],
  "data": {
    "description": "…",                  // plain-words explanation, with real counts
    "before": "SQL",                     // runs before any structure change
    "up": "SQL",                         // runs after additions, before removals
    "down": "SQL",                       // reverse of up, runs in Down after the removed structure is back
    "beforeDown": "SQL"                  // reverse of before, runs last in Down
  }
}
```

## Column

```jsonc
{
  "name": "status",
  "type": "text",                        // written as is in the SQL: text, numeric(10,2), timestamptz, uuid…
  "nullable": false,                     // default true
  "default": "now()",                    // SQL expression, written as is
  "check": "status IN ('a','b')", // inline CHECK; PostgreSQL names it <table>_<column>_check
  "description": "What the column means."
}
```

## createTables[]

```jsonc
{
  "name": "invoice",
  "description": "…",
  "schema": "public",                    // optional; qualifies the table in SQL ("public"."invoice")
  "near": "customer",                    // place the diagram box next to this table
  "columns": [ /* Column */ ],
  "primaryKey": {"name": "invoice_pk", "columns": ["id"]},   // default: <table>_pk on the first column
  "uniqueKeys": [{"name": "x_uk", "columns": ["a", "b"], "description": "…"}],
  "indexes": [
    {"name": "ix_x", "columns": ["a", {"name": "b", "order": "desc"}], "unique": false,
     "where": "b IS NOT NULL", "description": "…"}
  ],
  "checks": [{"name": "x_dates_ck", "expression": "end_date >= start_date", "description": "…"}]
}
```

## alterTables[]

```jsonc
{
  "name": "customer",
  "addColumns":   [ /* Column */ ],
  "alterColumns": [                      // only the keys present change
    {"name": "isbn", "type": "varchar(17)", "nullable": true, "default": null, "check": null,
     "description": "…", "rename": "isbn13"}
  ],
  "dropColumns":  ["old_col"],           // its references are dropped too; indexes using it must be dropped explicitly
  "addIndexes":   [ /* index */ ],
  "dropIndexes":  ["ix_old"],
  "addChecks":    [ /* check */ ],
  "dropChecks":   ["old_ck"],
  "addUniqueKeys":  [ /* unique key */ ],
  "dropUniqueKeys": ["old_uk"]
}
```

Notes:

- `"default": null` or `"check": null` removes it.
- A type change generates `ALTER … TYPE` without `USING`. When the data does not convert implicitly, do
  the conversion in `data.before` (`ALTER TABLE t ALTER c TYPE int USING c::int;`): the generated
  statement that follows then changes nothing.
- An inline column check is replaced as `<table>_<column>_check` (PostgreSQL's default name). If the
  constraint was created with another name, drop it with `dropChecks` / raw SQL instead.

## addReferences[]

```jsonc
{
  "name": "invoice_customer",            // constraint name
  "from": "invoice",                     // table holding the foreign key
  "columns": ["customer_id"],
  "to": "customer",                      // referenced table
  "toColumns": ["id"],                   // default: the primary key of `to`
  "onDelete": "restrict",                // none (default) | cascade | set null | restrict | set default
  "onUpdate": "none",
  "description": "…"
}
```

The reference is drawn between the closest displays of the two tables, with an orthogonal line that
avoids the other tables when possible. `Mandatory` (solid vs. optional end) follows the nullability of
the foreign-key columns.
