# Redgate Data Modeler XML (DatabaseModel 2.4)

The format exported by Redgate Data Modeler (formerly Vertabelo). `rdm.py` reads and writes it so that an
untouched model saves back byte for byte; use it rather than text substitutions.

## Layout of the file

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<DatabaseModel VersionId="2.4">
    <Name></Name>
    <Description></Description>
    <DatabaseEngine><Name>postgresql</Name><Version>17</Version></DatabaseEngine>
    <Counters>…</Counters>            <!-- last id handed out, per object kind -->
    <Tables>…</Tables>
    <Views/>
    <References>…</References>        <!-- foreign keys -->
    <Sequences/>
    <Notes/>
    <Areas>…</Areas>                  <!-- coloured frames grouping tables on the diagram -->
    <TableDisplays>…</TableDisplays>  <!-- position and size of each table box -->
    <ReferenceDisplays>…</ReferenceDisplays>  <!-- route of each foreign-key line -->
</DatabaseModel>
```

Formatting Redgate expects: 4-space indentation, empty text elements written `<Description></Description>`,
empty lists written `<Properties/>`, `<Views/>`. `rdm.Model.load` learns which tags the file writes in
which form and `save` reproduces it.

## Ids and counters

Every object has an `Id` made of a prefix and a number: `t12` (table), `c340` (column), `i8` (index),
`r55` (reference), `tc3` (table check), `a2` (alternate key = unique key), `td40` (table display),
`rd61` (reference display). `<Counters>` keeps, per kind, the last number used:

```xml
<Counter><Name>table</Name><Value>131</Value><Prefix>t</Prefix></Counter>
```

`rdm.Model.new_id(kind)` takes `max(counter, highest id in the file) + 1` and updates the counter, so ids
never collide even when an export has stale counters.

## Table

```xml
<Table Id="t5">
    <Name>invoice</Name>
    <Description></Description>
    <Columns>
        <Column Id="c60">
            <Name>id</Name>
            <Type>text</Type>
            <Description></Description>
            <Nullable>false</Nullable>
            <DefaultValue>gen_random_uuid()</DefaultValue>
            <CheckExpression></CheckExpression>
            <Properties/>
        </Column>
    </Columns>
    <PrimaryKey><Name>invoice_pk</Name><Columns><Column>c60</Column></Columns></PrimaryKey>
    <AlternateKeys>…</AlternateKeys>   <!-- AlternateKey: Name, Description, Columns/Column, Properties -->
    <Indexes>…</Indexes>               <!-- Index: Name, Description, Columns/Column{Column, Ascending}, Properties -->
    <TableChecks>…</TableChecks>       <!-- TableCheck: Name, Description, CheckExpression, Properties -->
    <AdditionalSQLBefore></AdditionalSQLBefore>
    <AdditionalSQLAfter></AdditionalSQLAfter>
    <Properties>
        <Property><Name>schema</Name><Value>public</Value></Property>
    </Properties>
</Table>
```

Index properties: `unique` = `true`, `where` = partial-index predicate.

## Reference (foreign key)

```xml
<Reference Id="r40">
    <Name>invoice_customer</Name>
    <Description></Description>
    <PKTable>t1</PKTable>          <!-- referenced table -->
    <FKTable>t5</FKTable>          <!-- table holding the foreign key -->
    <PKRole></PKRole>
    <FKRole></FKRole>
    <ReferenceColumns>
        <ReferenceColumn><PKColumn>c1</PKColumn><FKColumn>c61</FKColumn></ReferenceColumn>
    </ReferenceColumns>
    <Cardinality>0..*</Cardinality>
    <Mandatory>true</Mandatory>    <!-- FK columns NOT NULL -->
    <OnUpdateAction>None</OnUpdateAction>
    <OnDeleteAction>Cascade</OnDeleteAction>   <!-- None | Cascade | SetNull | Restrict | SetDefault -->
    <Properties/>
</Reference>
```

## Diagram

```xml
<TableDisplay Id="td4">
    <X>6000.5</X><Y>6400.5</Y><Width>242.0</Width><Height>210.0</Height>
    <LineColor>#000000</LineColor><FillColor>#ffffff</FillColor>
    <Table>t5</Table>
    <FixedSize>false</FixedSize>
</TableDisplay>

<ReferenceDisplay Id="rd1">
    <Color>#000000</Color>
    <Type>Vertical_4CP</Type>      <!-- Horizontal_2CP | Horizontal_4CP | Vertical_2CP | Vertical_4CP -->
    <ControlPoints>
        <ControlPoint><X>6208.5</X><Y>6407.5</Y></ControlPoint>
        …
    </ControlPoints>
    <PkTableDisplay>td3</PkTableDisplay>
    <FkTableDisplay>td4</FkTableDisplay>
    <Reference>r1</Reference>
</ReferenceDisplay>
```

- A table can have several displays (the same table drawn twice, often with `FixedSize` true and a dashed
  border). The scripts treat the first non-fixed one as primary.
- Coordinates end in `.5` in Redgate exports (half-pixel alignment); `apply_changes._fmt` keeps that.
- Rows are 14 px high, the header 20 px, with 3 px of padding above the first row.
- Control points: the first is on the PK-side box border, the last on the FK-side border; `_4CP` routes
  go out, turn, cross and turn back in.
- `Area`: `Name` plus `DisplayInfo` (X, Y, Width, Height, colours, `DashArray`, name position). Areas are
  drawn behind the tables; new tables are kept out of them.
