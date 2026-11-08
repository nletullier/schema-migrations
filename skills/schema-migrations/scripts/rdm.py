"""Shared helpers for reading and writing Redgate Data Modeler XML models (DatabaseModel 2.4).

Only the Python standard library is used. The writer reproduces Redgate's own formatting
(4-space indentation, `<Leaf></Leaf>` for empty text elements, `<Containers/>` for empty lists,
the original XML declaration), so that loading and saving an untouched model yields the same bytes.
"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
INDENT = "    "

# Counter names used by Redgate, with the id prefix of the objects they number.
COUNTER_PREFIX = {
    "table": "t",
    "column": "c",
    "index": "i",
    "reference": "r",
    "table_check": "tc",
    "alternate_key": "a",
    "tableDisplay": "td",
    "referenceDisplay": "rd",
}


# --------------------------------------------------------------------------- XML I/O

class Model:
    """A loaded model plus the formatting facts needed to write it back unchanged."""

    def __init__(self, tree: ET.ElementTree, declaration: str, self_closing: set[str], open_close: set[str]):
        self.tree = tree
        self.root = tree.getroot()
        self.declaration = declaration
        self.self_closing = self_closing
        self.open_close = open_close

    # -- persistence -------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "Model":
        raw = Path(path).read_text(encoding="utf-8")
        first = raw.split("\n", 1)[0]
        declaration = first if first.startswith("<?xml") else DECLARATION
        self_closing = set(re.findall(r"<([A-Za-z_][\w.-]*)/>", raw))
        open_close = set(re.findall(r"<([A-Za-z_][\w.-]*)></\1>", raw))
        return cls(ET.ElementTree(ET.fromstring(raw.encode("utf-8"))), declaration, self_closing, open_close)

    @classmethod
    def empty(cls, name: str = "", engine: str = "postgresql", version: str = "17") -> "Model":
        root = ET.Element("DatabaseModel", {"VersionId": "2.4"})
        for tag, text in (("Name", name), ("Description", "")):
            _child(root, tag, text)
        eng = _child(root, "DatabaseEngine")
        _child(eng, "Name", engine)
        _child(eng, "Version", version)
        counters = _child(root, "Counters")
        for cname, prefix in list(COUNTER_PREFIX.items()) + [("area", "ar"), ("text", "tx"), ("note", "n")]:
            c = _child(counters, "Counter")
            _child(c, "Name", cname)
            _child(c, "Value", "0")
            _child(c, "Prefix", prefix)
        for tag in ("AdditionalSQLBeforeCreate", "AdditionalSQLAfterCreate",
                    "AdditionalSQLBeforeDrop", "AdditionalSQLAfterDrop"):
            _child(root, tag, "")
        for tag in ("Tables", "ExternalTables", "Views", "MaterializedViews", "References", "Sequences",
                    "Notes", "Areas", "Texts", "TableDisplays", "ExternalTableDisplays", "ViewDisplays",
                    "MaterializedViewDisplays", "ReferenceDisplays", "Properties"):
            _child(root, tag)
        reindent(root, 0)
        return cls(ET.ElementTree(root), DECLARATION, set(), set())

    def save(self, path: str | Path) -> None:
        out: list[str] = [self.declaration, "\n"]
        self._write(self.root, out)
        out.append("\n")
        Path(path).write_text("".join(out), encoding="utf-8")

    def clone(self) -> "Model":
        return Model(copy.deepcopy(self.tree), self.declaration, set(self.self_closing), set(self.open_close))

    def _write(self, el: ET.Element, out: list[str]) -> None:
        attrs = "".join(f' {k}="{_esc_attr(v)}"' for k, v in el.attrib.items())
        children = list(el)
        text = el.text
        if not children and not (text and text.strip()) and text not in (None, ""):
            text = text  # whitespace-only leaf: keep as is
        if not children and (text is None or text == ""):
            if self._is_self_closing(el.tag):
                out.append(f"<{el.tag}{attrs}/>")
            else:
                out.append(f"<{el.tag}{attrs}></{el.tag}>")
        else:
            out.append(f"<{el.tag}{attrs}>")
            if text:
                out.append(_esc_text(text))
            for ch in children:
                self._write(ch, out)
                if ch.tail:
                    out.append(_esc_text(ch.tail))
            out.append(f"</{el.tag}>")

    def _is_self_closing(self, tag: str) -> bool:
        if tag in self.self_closing:
            return True
        if tag in self.open_close:
            return False
        # Redgate's convention: empty lists (plural tags) are self-closed, empty leaves are not.
        return tag.endswith("s") and tag not in ("CheckExpression",)

    # -- lookups -----------------------------------------------------------
    def section(self, tag: str) -> ET.Element:
        el = self.root.find(tag)
        if el is None:
            el = ET.SubElement(self.root, tag)
        return el

    def tables(self) -> list[ET.Element]:
        return list(self.section("Tables"))

    def table(self, name: str) -> ET.Element | None:
        for t in self.tables():
            if t.findtext("Name") == name:
                return t
        return None

    def table_by_id(self, tid: str) -> ET.Element | None:
        for t in self.tables():
            if t.get("Id") == tid:
                return t
        return None

    def references(self) -> list[ET.Element]:
        return list(self.section("References"))

    def reference(self, name: str) -> ET.Element | None:
        for r in self.references():
            if r.findtext("Name") == name:
                return r
        return None

    def table_displays(self, table_id: str | None = None) -> list[ET.Element]:
        tds = list(self.section("TableDisplays"))
        return [d for d in tds if table_id is None or d.findtext("Table") == table_id]

    def reference_displays(self, ref_id: str | None = None) -> list[ET.Element]:
        rds = list(self.section("ReferenceDisplays"))
        return [d for d in rds if ref_id is None or d.findtext("Reference") == ref_id]

    def all_ids(self) -> set[str]:
        return {el.get("Id") for el in self.root.iter() if el.get("Id")}

    # -- id allocation -----------------------------------------------------
    def new_id(self, counter: str) -> str:
        prefix = COUNTER_PREFIX[counter]
        counters = self.section("Counters")
        node = None
        for c in counters:
            if c.findtext("Name") == counter:
                node = c
        if node is None:
            node = _child(counters, "Counter")
            _child(node, "Name", counter)
            _child(node, "Value", "0")
            _child(node, "Prefix", prefix)
            reindent(counters, 1)
        current = int(node.findtext("Value") or "0")
        used = [int(m.group(1)) for i in self.all_ids() if (m := re.fullmatch(re.escape(prefix) + r"(\d+)", i))]
        nxt = max([current] + used) + 1
        node.find("Value").text = str(nxt)
        return f"{prefix}{nxt}"


# --------------------------------------------------------------------------- element helpers

def _child(parent: ET.Element, tag: str, text: str | None = None, attrib: dict | None = None) -> ET.Element:
    el = ET.SubElement(parent, tag, attrib or {})
    if text is not None:
        el.text = text
    return el


def child(parent: ET.Element, tag: str, text: str | None = None, attrib: dict | None = None) -> ET.Element:
    return _child(parent, tag, text, attrib)


def reindent(el: ET.Element, level: int) -> None:
    """Set whitespace text/tails of `el`'s subtree for Redgate's 4-space layout.
    `level` is the depth of `el` itself (root = 0)."""
    children = list(el)
    if children:
        el.text = "\n" + INDENT * (level + 1)
        for i, ch in enumerate(children):
            reindent(ch, level + 1)
            ch.tail = "\n" + INDENT * (level + 1) if i < len(children) - 1 else "\n" + INDENT * level
    elif el.text is not None and not el.text.strip():
        el.text = ""


def append_indented(parent: ET.Element, el: ET.Element, parent_level: int) -> None:
    """Append `el` at the end of `parent`, fixing tails so the file stays aligned."""
    children = list(parent)
    if children:
        children[-1].tail = "\n" + INDENT * (parent_level + 1)
    else:
        parent.text = "\n" + INDENT * (parent_level + 1)
    parent.append(el)
    reindent(el, parent_level + 1)
    el.tail = "\n" + INDENT * parent_level


def remove_child(parent: ET.Element, el: ET.Element, parent_level: int) -> None:
    children = list(parent)
    idx = children.index(el)
    parent.remove(el)
    rest = list(parent)
    if not rest:
        parent.text = None
    elif idx == len(children) - 1:
        rest[-1].tail = "\n" + INDENT * parent_level


def level_of(model: Model, target: ET.Element) -> int:
    def walk(el, depth):
        if el is target:
            return depth
        for ch in el:
            r = walk(ch, depth + 1)
            if r is not None:
                return r
        return None
    lvl = walk(model.root, 0)
    if lvl is None:
        raise ValueError("element not in model")
    return lvl


def _esc_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(s: str) -> str:
    return _esc_text(s).replace('"', "&quot;")


# --------------------------------------------------------------------------- read-only views

@dataclass
class Column:
    id: str
    name: str
    type: str
    nullable: bool
    default: str
    check: str
    description: str


@dataclass
class Table:
    id: str
    name: str
    description: str
    schema: str | None
    columns: list[Column]
    pk: list[str]                      # column ids
    pk_name: str
    indexes: list[dict] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    alternate_keys: list[dict] = field(default_factory=list)

    def col(self, cid: str) -> Column | None:
        return next((c for c in self.columns if c.id == cid), None)

    def col_by_name(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def qualified(self) -> str:
        return f'"{self.schema}"."{self.name}"' if self.schema else f'"{self.name}"'


@dataclass
class Reference:
    id: str
    name: str
    pk_table: str
    fk_table: str
    pairs: list[tuple[str, str]]       # (pk column id, fk column id)
    mandatory: bool
    on_delete: str
    on_update: str


def table_view(el: ET.Element) -> Table:
    cols = []
    for c in el.find("Columns") if el.find("Columns") is not None else []:
        cols.append(Column(
            id=c.get("Id"), name=c.findtext("Name") or "", type=c.findtext("Type") or "",
            nullable=(c.findtext("Nullable") or "true").strip() == "true",
            default=c.findtext("DefaultValue") or "", check=c.findtext("CheckExpression") or "",
            description=c.findtext("Description") or ""))
    pk = el.find("PrimaryKey")
    pk_cols = [x.text for x in pk.find("Columns")] if pk is not None and pk.find("Columns") is not None else []
    schema = None
    for p in el.findall("./Properties/Property"):
        if p.findtext("Name") == "schema" and (p.findtext("Value") or "").strip():
            schema = p.findtext("Value").strip()
    indexes = []
    for ix in el.findall("./Indexes/Index"):
        props = {p.findtext("Name"): p.findtext("Value") for p in ix.findall("./Properties/Property")}
        indexes.append({
            "id": ix.get("Id"), "name": ix.findtext("Name"),
            "columns": [(c.findtext("Column"), (c.findtext("Ascending") or "true") == "true")
                        for c in ix.findall("./Columns/Column")],
            "unique": (props.get("unique") or "").lower() == "true",
            "where": props.get("where") or None,
        })
    checks = [{"id": tc.get("Id"), "name": tc.findtext("Name"), "expression": tc.findtext("CheckExpression") or "",
               "description": tc.findtext("Description") or ""} for tc in el.findall("./TableChecks/TableCheck")]
    aks = [{"id": a.get("Id"), "name": a.findtext("Name"),
            "columns": [x.text for x in a.findall("./Columns/Column")]} for a in el.findall("./AlternateKeys/AlternateKey")]
    return Table(id=el.get("Id"), name=el.findtext("Name") or "", description=el.findtext("Description") or "",
                 schema=schema, columns=cols, pk=pk_cols, pk_name=(pk.findtext("Name") if pk is not None else "") or "",
                 indexes=indexes, checks=checks, alternate_keys=aks)


def reference_view(el: ET.Element) -> Reference:
    pairs = [(rc.findtext("PKColumn"), rc.findtext("FKColumn")) for rc in el.findall("./ReferenceColumns/ReferenceColumn")]
    return Reference(id=el.get("Id"), name=el.findtext("Name") or "", pk_table=el.findtext("PKTable"),
                     fk_table=el.findtext("FKTable"), pairs=pairs,
                     mandatory=(el.findtext("Mandatory") or "false") == "true",
                     on_delete=el.findtext("OnDeleteAction") or "None", on_update=el.findtext("OnUpdateAction") or "None")


def schema_snapshot(model: Model) -> dict:
    """Name-keyed view of a model, used to compare two versions."""
    tables = {}
    by_id = {}
    for t in model.tables():
        tv = table_view(t)
        tables[tv.name] = tv
        by_id[tv.id] = tv
    refs = {}
    for r in model.references():
        rv = reference_view(r)
        refs[rv.name] = rv
    return {"tables": tables, "by_id": by_id, "references": refs}


# --------------------------------------------------------------------------- geometry

_NARROW = set("iljtfrI.,:;'!|()[] ")
_WIDE = set("mwMW")


def text_width(s: str, size: float = 12.0, bold: bool = False) -> float:
    """Rough Arial text width (no font metrics available in the standard library)."""
    w = 0.0
    for ch in s:
        if ch in _NARROW:
            w += 0.28
        elif ch in _WIDE:
            w += 0.85
        elif ch.isupper():
            w += 0.68
        elif ch.isdigit():
            w += 0.56
        else:
            w += 0.53
    return w * size * (1.08 if bold else 1.0)


def column_flags(table: Table, col: Column, fk_cols: set[str]) -> str:
    parts = []
    if col.id in table.pk:
        parts.append("PK")
    elif col.nullable:
        parts.append("N")
    if col.id in fk_cols:
        parts.append("FK")
    return " ".join(parts)


def display_rect(td: ET.Element) -> tuple[float, float, float, float]:
    return (float(td.findtext("X")), float(td.findtext("Y")), float(td.findtext("Width")), float(td.findtext("Height")))
