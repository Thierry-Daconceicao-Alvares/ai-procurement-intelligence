// Deserializes the TMDL folder of the semantic model with the Tabular Object
// Model and prints what it contains. Any syntax or reference error in the
// TMDL files makes the deserialization, and this program, fail.
//
// Usage: dotnet run --project scripts/powerbi/tmdl-check -- <definition folder>

using Microsoft.AnalysisServices.Tabular;

var folder = args.Length > 0
    ? args[0]
    : Path.Combine("powerbi", "ProcurementIntelligence.SemanticModel", "definition");

try
{
    var database = TmdlSerializer.DeserializeDatabaseFromFolder(folder);
    var model = database.Model;
    Console.WriteLine($"Compatibility level: {database.CompatibilityLevel}");
    Console.WriteLine($"Culture: {model.Culture}");
    foreach (var table in model.Tables)
    {
        var columns = table.Columns.Count(c => c.Type != ColumnType.RowNumber);
        var partitions = string.Join(", ", table.Partitions.Select(p => p.SourceType.ToString()));
        Console.WriteLine(
            $"Table {table.Name}: {columns} columns, {table.Measures.Count} measures, "
            + $"partitions: {partitions}");
    }
    foreach (var relationship in model.Relationships.OfType<SingleColumnRelationship>())
    {
        Console.WriteLine(
            $"Relationship {relationship.FromTable.Name}[{relationship.FromColumn.Name}] -> "
            + $"{relationship.ToTable.Name}[{relationship.ToColumn.Name}] "
            + $"({relationship.FromCardinality}-to-{relationship.ToCardinality}, "
            + $"{relationship.CrossFilteringBehavior})");
    }
    var measures = model.Tables.Sum(t => t.Measures.Count);
    var totalColumns = model.Tables.Sum(t => t.Columns.Count(c => c.Type != ColumnType.RowNumber));
    Console.WriteLine(
        $"Total: {model.Tables.Count} tables, {totalColumns} columns, {measures} measures, "
        + $"{model.Relationships.Count} relationships, {model.Expressions.Count} expressions");
    return 0;
}
catch (Exception error)
{
    Console.WriteLine($"::error title=TMDL::{error.GetType().Name}: {error.Message}");
    if (error.InnerException is not null)
    {
        Console.WriteLine($"::error title=TMDL::{error.InnerException.Message}");
    }
    return 1;
}
