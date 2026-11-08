using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Design;

namespace MyApp.Data;

/// <summary>
/// Context for the <c>dotnet ef</c> tools (add-migration.sh, apply-migrations.sh).
///
/// The database is <c>ConnectionStrings__Data</c>, from the environment. Adding a migration does not
/// connect, so it works without it; applying one fails with EF's "no connection string" error.
/// </summary>
public sealed class AppDbContextFactory : IDesignTimeDbContextFactory<AppDbContext>
{
    public AppDbContext CreateDbContext(string[] args)
    {
        var options = new DbContextOptionsBuilder<AppDbContext>();
        if (ConnectionString() is { } connectionString) options.UseNpgsql(connectionString);
        else options.UseNpgsql();
        return new AppDbContext(options.Options);
    }

    public static string? ConnectionString()
        => Environment.GetEnvironmentVariable("ConnectionStrings__Data") is { Length: > 0 } value ? value : null;
}
