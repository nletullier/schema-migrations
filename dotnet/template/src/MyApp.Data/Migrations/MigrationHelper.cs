using System.Reflection;
using System.Runtime.CompilerServices;

namespace MyApp.Data.Migrations;

/// <summary>
/// Reads a migration's embedded SQL: <c>{migration id}.Up.sql</c> or <c>.Down.sql</c>, named after the C#
/// file that calls it. The file goes to the server as one batch: PostgreSQL accepts several statements per
/// command and its DDL is transactional, and EF wraps the migration in a transaction, so a failure halfway
/// leaves no half-applied schema.
/// </summary>
public static class MigrationHelper
{
    public static string Load(string direction = "Up", [CallerFilePath] string sourceFilePath = "")
    {
        var migrationId = Path.GetFileNameWithoutExtension(sourceFilePath);
        var resourceName = $"{typeof(MigrationHelper).Namespace}.{migrationId}.{direction}.sql";

        using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(resourceName)
            ?? throw new FileNotFoundException($"Embedded SQL not found: {resourceName}.");
        using var reader = new StreamReader(stream);
        return reader.ReadToEnd();
    }
}
