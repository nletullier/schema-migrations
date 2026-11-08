using Microsoft.EntityFrameworkCore;

namespace MyApp.Data;

/// <summary>
/// This context exists for migrations only: it describes <b>no</b> entity.
///
/// The schema is written in SQL, one <c>.Up.sql</c> and one <c>.Down.sql</c> per migration, and the
/// application reads and writes it however it likes (Dapper, ADO.NET, another ORM). EF Core only brings
/// its tooling: ordering, the <c>__EFMigrationsHistory</c> journal, the transaction and the lock.
/// </summary>
public class AppDbContext(DbContextOptions<AppDbContext> options) : DbContext(options);
