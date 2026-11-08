# Database-first migrations with .NET and EF Core

The schema is written in SQL, one `Up.sql` and one `Down.sql` per migration, and EF Core is used **only as
the migration runner**: it orders the migrations, records them in `__EFMigrationsHistory`, wraps each one
in a transaction and rolls them back on demand. No entity, no model snapshot to keep in sync, no C#
description of the schema.

With the schema-migrations plugin, every migration is a folder that also holds its spec, the data model
after it, two diagrams and a "what's new" note. The plugin writes the SQL; EF runs it.

[`template/`](template) is a minimal working setup (PostgreSQL, .NET 10, EF Core 10) to copy into a solution.
It was checked end to end:

- two migrations created by the plugin;
- applied one at a time, with the data step;
- rolled back to zero;
- deployed with a migration bundle.

## Why this setup

**One source of truth.** In code-first, the schema is described twice: by the C# model and by the database
(plus a model snapshot in between). The three drift, and `dotnet ef migrations add` can produce a migration
nobody asked for. Here the SQL is the only description. The plugin's `model.xml` and diagrams are generated
from the same spec, so the documentation cannot drift either.

**All of PostgreSQL, not the subset an ORM maps.** The SQL is written as you would write it by hand:

- partial and expression indexes, check constraints, function defaults (`gen_random_uuid()`, typeids…);
- `ON DELETE` rules, enums, extensions, functions, triggers;
- data migrations (backfills, splits, merges) in the same transaction as the structure they need.

Nothing waits for EF to support it.

**EF's runner for free.** You get the tooling everybody already knows, without its modelling layer:

- migrations applied in order, each in a transaction (PostgreSQL DDL is transactional: a failure leaves
  nothing half applied);
- a history table, so every environment knows where it stands;
- a lock (EF 9 and later) when several instances start and migrate at once;
- `dotnet ef database update <migration>` to go back, running the `Down.sql` files in reverse order;
- three deployment modes: at application startup, as a self-contained bundle, or as an idempotent SQL
  script for a DBA.

**Any data access.** The application reads and writes the database however it likes: Dapper, plain
ADO.NET, or EF with a hand-written or scaffolded model. The migrations project depends on none of it, and
it builds in seconds.

**Reviewable.** A pull request shows the SQL that will run, exactly. With the plugin, it also shows what
changes on the diagram and a note that explains why and how to deploy. Claude prepares the migration and
stops until you approve it.

**Small blast radius.** A migration is a folder you can read on its own. Nothing else in the solution
changes when the schema does.

## What it costs

- **You own the Down.** The plugin writes it from the spec (drops, reverse alterations, data steps), but
  read it: a Down cannot bring back the rows that Up deleted.
- **No generated model for LINQ.** If part of the application queries with EF, keep that model separate.
  You can scaffold it (`dotnet ef dbcontext scaffold`) or write it by hand. Never let it generate migrations.
- **Statements that refuse a transaction** need a migration of their own, with
  `migrationBuilder.Sql(sql, suppressTransaction: true)` in its C# shell. Example: `CREATE INDEX CONCURRENTLY`.
- **The usual discipline:**
  - never edit a migration that has been applied anywhere;
  - drop a column in two releases when an older version of the application may still be running.

## Layout

```
add-migration.sh            creates a migration: EF shell + empty Up/Down, in its own folder
apply-migrations.sh         dotnet ef database update [target]
.schema-migrations.json     tells the plugin where migrations live and how to create one
src/MyApp.Data/
├── MyApp.Data.csproj       embeds Migrations/**/*.sql under a folder-free name
├── AppDbContext.cs         a DbContext with no entity
├── AppDbContextFactory.cs  design-time factory: ConnectionStrings__Data
└── Migrations/
    ├── MigrationHelper.cs          loads {id}.Up.sql / {id}.Down.sql
    ├── AppDbContextModelSnapshot.cs  (generated once, stays empty)
    ├── baseline.xml                 (optional) the model the first migration starts from
    └── 20261108114517_AddPublishers/
        ├── 20261108114517_AddPublishers.cs           Up => Sql(Load("Up")), Down => Sql(Load("Down"))
        ├── 20261108114517_AddPublishers.Designer.cs  carries [Migration("…")]: keep it
        ├── 20261108114517_AddPublishers.Up.sql
        ├── 20261108114517_AddPublishers.Down.sql
        ├── changes.json  model.xml  diff.svg  schema.svg  20261108114517_AddPublishers.md
```

## Setting it up

1. **Copy the template** into the solution and rename `MyApp.Data`. Change it in five places:
   - the folder and the `.csproj` name;
   - the namespaces;
   - `DATA_PROJECT`, `MIGRATIONS_DIR` and `NAMESPACE` at the top of both scripts;
   - the paths in `.schema-migrations.json`.

2. **Install the EF tools** once: `dotnet tool install --global dotnet-ef`, or as a local tool in a tool
   manifest so the version is pinned with the repository.

3. **Point at a database** with the `ConnectionStrings__Data` environment variable (or replace
   `AppDbContextFactory.ConnectionString()` by your own configuration). Adding a migration does not
   connect; applying one does.

4. **Start from the existing schema**, since the database is there first. There are two cases.

   *New environments must be able to be built from scratch.* Make the first migration a `Baseline` whose
   `Up.sql` is the current schema (`pg_dump --schema-only --no-owner`, cleaned up). On the databases that
   already have that schema, mark it as applied instead of running it:

   ```sql
   INSERT INTO "__EFMigrationsHistory" ("MigrationId", "ProductVersion")
   VALUES ('20260101000000_Baseline', '10.0.11');
   ```

   *Only existing databases matter.* Leave the `Baseline` SQL empty, or skip it, and start with the first
   real change.

   For the plugin, export the current model from Redgate Data Modeler as `baseline.xml`. Without one, the
   plugin starts from an empty model and only draws what the migrations add.

5. **Let the application migrate itself** if that is how you deploy:

   ```csharp
   builder.Services.AddDbContext<AppDbContext>(o => o.UseNpgsql(connectionString));
   // …
   if (!app.Environment.IsDevelopment())
   {
       using var scope = app.Services.CreateScope();
       await scope.ServiceProvider.GetRequiredService<AppDbContext>().Database.MigrateAsync();
   }
   ```

   Failing at startup is better than serving requests on a schema that is behind. Development is left out
   on purpose: the developer applies migrations with the script, when they are ready.

## Day to day

With the plugin, ask Claude for the change. It:

1. runs `./add-migration.sh` through the `create` command of `.schema-migrations.json`;
2. writes `changes.json`;
3. builds the SQL, the model and the review files;
4. stops for your review.

Then apply:

```bash
./apply-migrations.sh                 # everything pending
./apply-migrations.sh AddPublishers   # up or down to this one
./apply-migrations.sh 0               # everything rolled back (Down.sql files, newest first)
```

Without the plugin, run `./add-migration.sh AddInvoices` and write the two SQL files yourself.

## Deploying

| Mode | Command | When |
|---|---|---|
| At startup | `Database.MigrateAsync()` (above) | One application owns the database; the deploy fails if a migration does |
| Bundle | `dotnet ef migrations bundle --project src/MyApp.Data/MyApp.Data.csproj --startup-project src/MyApp.Data/MyApp.Data.csproj --output efbundle`, then `./efbundle --connection "…"` | A pipeline step before the new version starts; no SDK needed on the target |
| SQL script | `dotnet ef migrations script --idempotent … -o migrate.sql` | A DBA reviews and runs it; every migration is guarded by its history row |

## Pitfalls

- **Keep the resource name folder-free.** `MigrationHelper` finds the SQL as
  `{namespace}.{migration id}.Up.sql`, from the name of the C# file that calls it. That is why the csproj
  sets `LogicalName` and why the `.cs` file must keep the migration id as its name. Without
  `LogicalName`, MSBuild prefixes the folder (with an underscore, since it starts with a digit) and the
  lookup fails.
- **Keep the `.Designer.cs`.** It carries the `[Migration("id")]` attribute EF orders by.
- **One batch per file.** PostgreSQL accepts several statements in one command, so each `.sql` is sent as
  is. Other engines (SQL Server and its `GO`) would need the file split.
- **The snapshot stays empty**, and that is fine: there is no model to compare it with.
- **Moving existing migrations into folders** does not change anything for EF, as long as the ids and the
  resource names do not change.
