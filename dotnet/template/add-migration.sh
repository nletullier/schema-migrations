#!/usr/bin/env bash
# Creates an EF Core migration whose body is plain SQL, in its own folder:
#   ./add-migration.sh AddInvoices
#   → src/MyApp.Data/Migrations/{timestamp}_AddInvoices/{id}.cs, {id}.Designer.cs, {id}.Up.sql, {id}.Down.sql
#
# The schema is not described by an EF model: the generated C# is replaced by a shell that loads the SQL
# files (MigrationHelper). The schema-migrations plugin runs this script (`create` in .schema-migrations.json)
# and then fills the SQL from its spec.
set -euo pipefail

DATA_PROJECT="src/MyApp.Data/MyApp.Data.csproj"
MIGRATIONS_DIR="src/MyApp.Data/Migrations"
NAMESPACE="MyApp.Data.Migrations"
CONTEXT="AppDbContext"

cd "$(dirname "${BASH_SOURCE[0]}")"
NAME="${1:?Usage: ./add-migration.sh MigrationName}"

dotnet restore "$DATA_PROJECT" --verbosity quiet   # dotnet ef reads the assets file before it builds
dotnet ef migrations add "$NAME" --project "$DATA_PROJECT" --startup-project "$DATA_PROJECT" \
  --context "$CONTEXT" --output-dir Migrations

FILE=$(ls -t "$MIGRATIONS_DIR"/*_"$NAME".cs | grep -v Designer | head -n 1)
ID=$(basename "$FILE" .cs)

# One migration, one folder: everything about it sits together.
mkdir -p "$MIGRATIONS_DIR/$ID"
mv "$MIGRATIONS_DIR/$ID.cs" "$MIGRATIONS_DIR/$ID.Designer.cs" "$MIGRATIONS_DIR/$ID/"

cat > "$MIGRATIONS_DIR/$ID/$ID.Up.sql" <<SQL
-- Migration: $NAME
SQL
cat > "$MIGRATIONS_DIR/$ID/$ID.Down.sql" <<SQL
-- Rollback of migration: $NAME
SQL

cat > "$MIGRATIONS_DIR/$ID/$ID.cs" <<CSHARP
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace $NAMESPACE
{
    /// <inheritdoc />
    public partial class $NAME : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
            => migrationBuilder.Sql(MigrationHelper.Load("Up"));

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
            => migrationBuilder.Sql(MigrationHelper.Load("Down"));
    }
}
CSHARP

echo "Created $MIGRATIONS_DIR/$ID/"
