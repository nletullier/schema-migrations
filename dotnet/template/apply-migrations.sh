#!/usr/bin/env bash
# Applies the pending migrations to the database named by ConnectionStrings__Data.
#   ./apply-migrations.sh                 # all pending migrations
#   ./apply-migrations.sh AddInvoices     # up or down to this migration (its Down.sql runs when going back)
#   ./apply-migrations.sh 0               # everything rolled back
set -euo pipefail

DATA_PROJECT="src/MyApp.Data/MyApp.Data.csproj"

cd "$(dirname "${BASH_SOURCE[0]}")"
dotnet restore "$DATA_PROJECT" --verbosity quiet
dotnet ef database update ${1:-} --project "$DATA_PROJECT" --startup-project "$DATA_PROJECT" --context AppDbContext
