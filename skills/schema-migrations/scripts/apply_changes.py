#!/usr/bin/env python3
"""Apply a JSON change spec to a Redgate Data Modeler XML model and generate the Up/Down SQL.

    python3 -I apply_changes.py --model base.xml --spec changes.json --out new.xml \
        --up Migration.Up.sql --down Migration.Down.sql

    python3 -I apply_changes.py --init --out model.xml     # start an empty model

The spec format is documented in references/spec-format.md. The base model is never modified:
the result goes to --out, so the spec can be edited and re-applied to the same base as often as needed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rdm  # noqa: E402
from rdm import child  # noqa: E402

ON_ACTION = {
    "": "None", "none": "None", "no action": "None",
    "cascade": "Cascade",
    "set null": "SetNull", "setnull": "SetNull",
    "restrict": "Restrict",
    "set default": "SetDefault", "setdefault": "SetDefault",
}
ON_ACTION_SQL = {"Cascade": "CASCADE", "SetNull": "SET NULL", "Restrict": "RESTRICT", "SetDefault": "SET DEFAULT"}

ROW_H = 14.0
HEADER_H = 20.0
TOP_PAD = 3.0


class SpecError(Exception):
    pass


# =========================================================================== model edits

class Editor:
    def __init__(self, model: rdm.Model):
        self.m = model
        self.created_tables: list[str] = []
        self.warnings: list[str] = []
        # boxes of dropped tables stay occupied: the diff diagram still draws them, in red
        self.ghost_rects: list[tuple[float, float, float, float]] = []

    # ----------------------------------------------------------------- tables
    def create_table(self, spec: dict) -> None:
        name = spec["name"]
        if self.m.table(name) is not None:
            raise SpecError(f"table {name} already exists")
        tables = self.m.section("Tables")
        t = rdm.ET.Element("Table", {"Id": self.m.new_id("table")})
        child(t, "Name", name)
        child(t, "Description", spec.get("description", ""))
        cols = child(t, "Columns")
        ids = {}
        for c in spec["columns"]:
            ids[c["name"]] = self._column_el(cols, c).get("Id")
        pk_spec = spec.get("primaryKey") or {"name": f"{name}_pk", "columns": [spec["columns"][0]["name"]]}
        pk = child(t, "PrimaryKey")
        child(pk, "Name", pk_spec.get("name", f"{name}_pk"))
        pkc = child(pk, "Columns")
        for cn in pk_spec["columns"]:
            child(pkc, "Column", self._req(ids, cn, name))
        aks = child(t, "AlternateKeys")
        for uk in spec.get("uniqueKeys", []):
            self._ak_el(aks, uk, ids, name)
        ixs = child(t, "Indexes")
        for ix in spec.get("indexes", []):
            self._index_el(ixs, ix, ids, name)
        tcs = child(t, "TableChecks")
        for ck in spec.get("checks", []):
            self._check_el(tcs, ck)
        child(t, "AdditionalSQLBefore", "")
        child(t, "AdditionalSQLAfter", "")
        props = child(t, "Properties")
        if spec.get("schema"):
            p = child(props, "Property")
            child(p, "Name", "schema")
            child(p, "Value", spec["schema"])
        rdm.append_indented(tables, t, 1)
        self.created_tables.append(name)
        self._place_display(t, spec.get("near"))

    def drop_table(self, name: str) -> None:
        t = self._table(name)
        tid = t.get("Id")
        for r in list(self.m.references()):
            if r.findtext("PKTable") == tid or r.findtext("FKTable") == tid:
                self.drop_reference(r.findtext("Name"))
        for td in self.m.table_displays(tid):
            self.ghost_rects.append(rdm.display_rect(td))
            rdm.remove_child(self.m.section("TableDisplays"), td, 1)
        rdm.remove_child(self.m.section("Tables"), t, 1)

    def alter_table(self, spec: dict) -> None:
        name = spec["name"]
        t = self._table(name)
        tlevel = 2
        cols = t.find("Columns")
        for c in spec.get("addColumns", []):
            if self._col(t, c["name"], must=False) is not None:
                raise SpecError(f"column {name}.{c['name']} already exists")
            self._column_el(cols, c, level=tlevel + 1)
            for td in self.m.table_displays(t.get("Id")):
                td.find("Height").text = _fmt(float(td.findtext("Height")) + ROW_H)
        for c in spec.get("alterColumns", []):
            col = self._col(t, c["name"])
            for key, tag in (("type", "Type"), ("default", "DefaultValue"), ("check", "CheckExpression"),
                             ("description", "Description")):
                if key in c:
                    col.find(tag).text = c[key] or None
            if "nullable" in c:
                col.find("Nullable").text = "true" if c["nullable"] else "false"
            if "rename" in c:
                col.find("Name").text = c["rename"]
        for cn in spec.get("dropColumns", []):
            col = self._col(t, cn)
            cid = col.get("Id")
            for r in list(self.m.references()):
                if any(rc.findtext("FKColumn") == cid or rc.findtext("PKColumn") == cid
                       for rc in r.findall("./ReferenceColumns/ReferenceColumn")):
                    self.drop_reference(r.findtext("Name"))
            for ix in t.findall("./Indexes/Index"):
                if any(c.findtext("Column") == cid for c in ix.findall("./Columns/Column")):
                    raise SpecError(f"index {ix.findtext('Name')} uses {name}.{cn}: drop it too (dropIndexes)")
            rdm.remove_child(cols, col, tlevel + 1)
            for td in self.m.table_displays(t.get("Id")):
                td.find("Height").text = _fmt(max(HEADER_H + 30, float(td.findtext("Height")) - ROW_H))
        if spec.get("addColumns") or any("type" in c or "rename" in c for c in spec.get("alterColumns", [])):
            need_w, _ = self._size_for(t)
            for td in self.m.table_displays(t.get("Id")):
                if float(td.findtext("Width")) < need_w:
                    td.find("Width").text = _fmt(need_w)
        ids = {c.findtext("Name"): c.get("Id") for c in cols}
        ixs = t.find("Indexes")
        for ixn in spec.get("dropIndexes", []):
            el = next((i for i in ixs if i.findtext("Name") == ixn), None)
            if el is None:
                raise SpecError(f"index {ixn} not found on {name}")
            rdm.remove_child(ixs, el, tlevel + 1)
        for ix in spec.get("addIndexes", []):
            self._index_el(ixs, ix, ids, name, level=tlevel + 1)
        tcs = t.find("TableChecks")
        for ckn in spec.get("dropChecks", []):
            el = next((i for i in tcs if i.findtext("Name") == ckn), None)
            if el is None:
                raise SpecError(f"check {ckn} not found on {name}")
            rdm.remove_child(tcs, el, tlevel + 1)
        for ck in spec.get("addChecks", []):
            self._check_el(tcs, ck, level=tlevel + 1)
        aks = t.find("AlternateKeys")
        for akn in spec.get("dropUniqueKeys", []):
            el = next((i for i in aks if i.findtext("Name") == akn), None)
            if el is None:
                raise SpecError(f"unique key {akn} not found on {name}")
            rdm.remove_child(aks, el, tlevel + 1)
        for uk in spec.get("addUniqueKeys", []):
            self._ak_el(aks, uk, ids, name, level=tlevel + 1)

    # ------------------------------------------------------------- references
    def add_reference(self, spec: dict) -> None:
        name = spec["name"]
        if self.m.reference(name) is not None:
            raise SpecError(f"reference {name} already exists")
        fk_t = self._table(spec["from"])
        pk_t = self._table(spec["to"])
        fk_cols = spec["columns"]
        pk_view = rdm.table_view(pk_t)
        pk_cols = spec.get("toColumns") or [pk_view.col(cid).name for cid in pk_view.pk]
        if len(pk_cols) != len(fk_cols):
            raise SpecError(f"reference {name}: column count mismatch")
        ref = rdm.ET.Element("Reference", {"Id": self.m.new_id("reference")})
        child(ref, "Name", name)
        child(ref, "Description", spec.get("description", ""))
        child(ref, "PKTable", pk_t.get("Id"))
        child(ref, "FKTable", fk_t.get("Id"))
        child(ref, "PKRole", "")
        child(ref, "FKRole", "")
        rcs = child(ref, "ReferenceColumns")
        mandatory = True
        for pc, fc in zip(pk_cols, fk_cols):
            rc = child(rcs, "ReferenceColumn")
            child(rc, "PKColumn", self._col(pk_t, pc).get("Id"))
            fcol = self._col(fk_t, fc)
            child(rc, "FKColumn", fcol.get("Id"))
            mandatory = mandatory and fcol.findtext("Nullable") == "false"
        child(ref, "Cardinality", "0..*")
        child(ref, "Mandatory", "true" if mandatory else "false")
        child(ref, "OnUpdateAction", _action(spec.get("onUpdate", "none")))
        child(ref, "OnDeleteAction", _action(spec.get("onDelete", "none")))
        child(ref, "Properties")
        rdm.append_indented(self.m.section("References"), ref, 1)
        self._reference_display(ref, pk_t, fk_t, pk_cols[0], fk_cols[0])

    def drop_reference(self, name: str) -> None:
        r = self.m.reference(name)
        if r is None:
            raise SpecError(f"reference {name} not found")
        for rd in self.m.reference_displays(r.get("Id")):
            rdm.remove_child(self.m.section("ReferenceDisplays"), rd, 1)
        rdm.remove_child(self.m.section("References"), r, 1)

    # ---------------------------------------------------------------- helpers
    def _table(self, name):
        t = self.m.table(name)
        if t is None:
            raise SpecError(f"table {name} not found in the model")
        return t

    def _col(self, t, name, must=True):
        for c in t.findall("./Columns/Column"):
            if c.findtext("Name") == name:
                return c
        if must:
            raise SpecError(f"column {t.findtext('Name')}.{name} not found")
        return None

    @staticmethod
    def _req(ids, cn, tname):
        if cn not in ids:
            raise SpecError(f"column {tname}.{cn} not found")
        return ids[cn]

    def _column_el(self, parent, c, level=None):
        el = rdm.ET.Element("Column", {"Id": self.m.new_id("column")})
        child(el, "Name", c["name"])
        child(el, "Type", c["type"])
        child(el, "Description", c.get("description", ""))
        child(el, "Nullable", "true" if c.get("nullable", True) else "false")
        child(el, "DefaultValue", c.get("default", "") or "")
        child(el, "CheckExpression", c.get("check", "") or "")
        child(el, "Properties")
        if level is None:
            parent.append(el)
        else:
            rdm.append_indented(parent, el, level)
        return el

    def _index_el(self, parent, ix, ids, tname, level=None):
        el = rdm.ET.Element("Index", {"Id": self.m.new_id("index")})
        child(el, "Name", ix["name"])
        child(el, "Description", ix.get("description", ""))
        cols = child(el, "Columns")
        for c in ix["columns"]:
            cname, asc = (c, True) if isinstance(c, str) else (c["name"], (c.get("order", "asc").lower() != "desc"))
            wrap = child(cols, "Column")
            child(wrap, "Column", self._req(ids, cname, tname))
            child(wrap, "Ascending", "true" if asc else "false")
        props = child(el, "Properties")
        if ix.get("unique"):
            p = child(props, "Property")
            child(p, "Name", "unique")
            child(p, "Value", "true")
        if ix.get("where"):
            p = child(props, "Property")
            child(p, "Name", "where")
            child(p, "Value", ix["where"])
        if level is None:
            parent.append(el)
        else:
            rdm.append_indented(parent, el, level)

    def _check_el(self, parent, ck, level=None):
        el = rdm.ET.Element("TableCheck", {"Id": self.m.new_id("table_check")})
        child(el, "Name", ck["name"])
        child(el, "Description", ck.get("description", ""))
        child(el, "CheckExpression", ck["expression"])
        child(el, "Properties")
        if level is None:
            parent.append(el)
        else:
            rdm.append_indented(parent, el, level)

    def _ak_el(self, parent, uk, ids, tname, level=None):
        el = rdm.ET.Element("AlternateKey", {"Id": self.m.new_id("alternate_key")})
        child(el, "Name", uk["name"])
        child(el, "Description", uk.get("description", ""))
        cols = child(el, "Columns")
        for cn in uk["columns"]:
            child(cols, "Column", self._req(ids, cn, tname))
        child(el, "Properties")
        if level is None:
            parent.append(el)
        else:
            rdm.append_indented(parent, el, level)

    # ---------------------------------------------------------------- layout
    def _size_for(self, t) -> tuple[float, float]:
        tv = rdm.table_view(t)
        fk_ids = self._fk_column_ids(t.get("Id"))
        # sized for bold text: a new table is drawn in bold in the diff view
        name_w = max([rdm.text_width(c.name, bold=True) for c in tv.columns] + [40])
        type_w = max([rdm.text_width(c.type, bold=True) for c in tv.columns] + [30])
        flag_w = max([rdm.text_width(rdm.column_flags(tv, c, fk_ids), bold=True) for c in tv.columns]
                     + [rdm.text_width("N FK", bold=True)])
        title_w = rdm.text_width(tv.name, bold=True) + 24
        width = max(4 + name_w + 16 + type_w + 16 + flag_w + 8, title_w, 150)
        height = HEADER_H + TOP_PAD + ROW_H * len(tv.columns) + 8
        return round(width), round(height)

    def _fk_column_ids(self, tid) -> set[str]:
        out = set()
        for r in self.m.references():
            if r.findtext("FKTable") == tid:
                out |= {rc.findtext("FKColumn") for rc in r.findall("./ReferenceColumns/ReferenceColumn")}
        return out

    def _primary_display(self, table_name):
        t = self.m.table(table_name)
        if t is None:
            return None
        tds = self.m.table_displays(t.get("Id"))
        if not tds:
            return None
        free = [d for d in tds if d.findtext("FixedSize") != "true"]
        return (free or tds)[0]

    def _occupied(self, exclude=None):
        rects = list(self.ghost_rects)
        for td in self.m.table_displays():
            if td is exclude:
                continue
            rects.append(rdm.display_rect(td))
        for ar in self.m.section("Areas"):
            di = ar.find("DisplayInfo")
            if di is not None:
                rects.append((float(di.findtext("X")), float(di.findtext("Y")),
                              float(di.findtext("Width")), float(di.findtext("Height"))))
        return rects

    def _place_display(self, t, near: str | None):
        w, h = self._size_for(t)
        anchor = self._primary_display(near) if near else None
        occupied = self._occupied()
        if anchor is not None:
            ax, ay, aw, ah = rdm.display_rect(anchor)
            cx, cy = ax + aw / 2, ay + ah / 2
        elif occupied:
            cx = max(r[0] + r[2] for r in occupied) + 80 + w / 2
            cy = min(r[1] for r in occupied) + h / 2
        else:
            cx, cy = 100 + w / 2, 100 + h / 2
        x, y = _free_spot(cx, cy, w, h, occupied, lines=self._line_segments())
        td = rdm.ET.Element("TableDisplay", {"Id": self.m.new_id("tableDisplay")})
        for tag, val in (("X", _fmt(x)), ("Y", _fmt(y)), ("Width", _fmt(w)), ("Height", _fmt(h)),
                         ("LineColor", "#000000"), ("FillColor", "#ffffff"), ("Table", t.get("Id")),
                         ("FixedSize", "false")):
            child(td, tag, val)
        rdm.append_indented(self.m.section("TableDisplays"), td, 1)

    def _line_segments(self):
        segs = []
        for rd in self.m.reference_displays():
            cps = [(float(cp.findtext("X")), float(cp.findtext("Y")))
                   for cp in rd.findall("./ControlPoints/ControlPoint")]
            segs += [(cps[i], cps[i + 1]) for i in range(len(cps) - 1)]
        return segs

    def _reference_display(self, ref, pk_t, fk_t, pk_col, fk_col):
        pk_tds = self.m.table_displays(pk_t.get("Id"))
        fk_tds = self.m.table_displays(fk_t.get("Id"))
        if not pk_tds or not fk_tds:
            self.warnings.append(f"reference {ref.findtext('Name')}: a table has no display, no line drawn")
            return
        best = min(((p, f) for p in pk_tds for f in fk_tds), key=lambda pf: _center_dist(pf[0], pf[1]))
        p, f = best
        prow = _row_y(p, pk_t, pk_col)
        frow = _row_y(f, fk_t, fk_col)
        obstacles = [rdm.display_rect(td) for td in self.m.table_displays() if td is not p and td is not f]
        lines = self._line_segments()
        kind, points = _route(rdm.display_rect(p), rdm.display_rect(f), prow, frow, obstacles, lines)
        rd = rdm.ET.Element("ReferenceDisplay", {"Id": self.m.new_id("referenceDisplay")})
        child(rd, "Color", "#000000")
        child(rd, "Type", kind)
        cps = child(rd, "ControlPoints")
        for x, y in points:
            cp = child(cps, "ControlPoint")
            child(cp, "X", _fmt(x))
            child(cp, "Y", _fmt(y))
        child(rd, "PkTableDisplay", p.get("Id"))
        child(rd, "FkTableDisplay", f.get("Id"))
        child(rd, "Reference", ref.get("Id"))
        rdm.append_indented(self.m.section("ReferenceDisplays"), rd, 1)


def _fmt(v: float) -> str:
    v = round(float(v) * 2) / 2
    return f"{v:.1f}"


def _action(s: str) -> str:
    key = (s or "none").strip().lower()
    if key not in ON_ACTION:
        raise SpecError(f"unknown ON DELETE/UPDATE action: {s}")
    return ON_ACTION[key]


def _intersects(a, b, margin):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw + margin <= bx or bx + bw + margin <= ax or ay + ah + margin <= by or by + bh + margin <= ay)


def _free_spot(cx, cy, w, h, occupied, margin=70.0, step=20.0, max_radius=4000.0, lines=(),
               slack=400.0, line_cost=150.0):
    """Top-left corner of a free w*h spot near (cx, cy).

    Rings of growing radius are searched until a spot clears every table; the search then goes on for
    `slack` more pixels, and the spot finally kept balances its distance against the existing reference
    lines it would cover (each costs `line_cost` pixels)."""
    found = []
    r = 0.0
    first = None
    while r <= max_radius and (first is None or r <= first + slack):
        n = max(1, int(r // step))
        for i in range(-n, n + 1):
            for (px, py) in ((cx + i * step, cy - n * step), (cx + i * step, cy + n * step),
                             (cx - n * step, cy + i * step), (cx + n * step, cy + i * step)):
                rect = (px - w / 2, py - h / 2, w, h)
                if rect[0] < 0 or rect[1] < 0:
                    continue
                if any(_intersects(rect, o, margin) for o in occupied):
                    continue
                covered = sum(_segment_hits(a, b, rect, 10.0) for a, b in lines)
                score = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 + line_cost * covered
                found.append((score, rect[0], rect[1]))
                if first is None:
                    first = r
        r += step
    if not found:
        return cx + 3000, cy
    _, x, y = min(found)
    return x + 0.5, y + 0.5


def _center_dist(a, b):
    ax, ay, aw, ah = rdm.display_rect(a)
    bx, by, bw, bh = rdm.display_rect(b)
    return ((ax + aw / 2) - (bx + bw / 2)) ** 2 + ((ay + ah / 2) - (by + bh / 2)) ** 2


def _row_y(td, t, col_name):
    x, y, w, h = rdm.display_rect(td)
    names = [c.findtext("Name") for c in t.findall("./Columns/Column")]
    idx = names.index(col_name) if col_name in names else 0
    return min(y + HEADER_H + TOP_PAD + ROW_H * idx + ROW_H / 2, y + h - 4)


def _segment_hits(a, b, rect, margin=6.0):
    """Does the axis-aligned segment a-b cross the rectangle (grown by margin)?"""
    x, y, w, h = rect
    x0, x1 = sorted((a[0], b[0]))
    y0, y1 = sorted((a[1], b[1]))
    return not (x1 < x - margin or x0 > x + w + margin or y1 < y - margin or y0 > y + h + margin)


def _crossings(points, obstacles) -> int:
    return sum(_segment_hits(points[i], points[i + 1], o)
               for i in range(len(points) - 1) for o in obstacles)


def _near_parallel(points, lines, gap=12.0) -> int:
    """Count existing line segments running alongside (closer than gap) a segment of the candidate."""
    n = 0
    for i in range(len(points) - 1):
        (ax, ay), (bx, by) = points[i], points[i + 1]
        for (cx, cy), (dx, dy) in lines:
            if ax == bx and cx == dx and abs(ax - cx) < gap:
                if min(ay, by) < max(cy, dy) and min(cy, dy) < max(ay, by):
                    n += 1
            elif ay == by and cy == dy and abs(ay - cy) < gap:
                if min(ax, bx) < max(cx, dx) and min(cx, dx) < max(ax, bx):
                    n += 1
    return n


def _route(p, f, prow, frow, obstacles=(), lines=()):
    """Orthogonal route between a PK display p and an FK display f, avoiding the other tables when it can.

    Candidates are tried from the most natural one outwards; the first that crosses no table wins,
    otherwise the one crossing the fewest; ties go to the one that runs alongside fewer existing lines."""
    px, py, pw, ph = p
    fx, fy, fw, fh = f
    cands = []
    if fx >= px + pw + 10 or fx + fw <= px - 10:
        x1, x2 = (px + pw, fx) if fx >= px + pw else (px, fx + fw)
        if abs(prow - frow) < 1:
            cands.append(("Horizontal_2CP", [(x1, prow), (x2, prow)]))
        lo, hi = sorted((x1, x2))
        mid = (lo + hi) / 2
        xs = [mid]
        step = 10.0
        k = 1
        while mid - k * step > lo + 12 or mid + k * step < hi - 12:
            for xm in (mid - k * step, mid + k * step):
                if lo + 12 <= xm <= hi - 12:
                    xs.append(xm)
            k += 1
        for xm in xs:
            cands.append(("Horizontal_4CP", [(x1, prow), (xm, prow), (xm, frow), (x2, frow)]))
    else:
        cx1, cx2 = px + pw / 2, fx + fw / 2
        y1, y2 = (py + ph, fy) if fy >= py + ph else (py, fy + fh)
        lo, hi = max(px, fx) + 10, min(px + pw, fx + fw) - 10
        if lo <= hi:
            cands.append(("Vertical_2CP", [((lo + hi) / 2, y1), ((lo + hi) / 2, y2)]))
        a, b = sorted((y1, y2))
        mid = (a + b) / 2
        ys = [mid] + [mid + d * k * 10.0 for k in range(1, int((b - a) / 20) + 1) for d in (-1, 1)]
        for ym in ys:
            if a + 8 <= ym <= b - 8 or ym == mid:
                cands.append(("Vertical_4CP", [(cx1, y1), (cx1, ym), (cx2, ym), (cx2, y2)]))
    if not cands:
        return "Horizontal_2CP", [(px + pw, prow), (fx, frow)]
    scored = [(_crossings(pts, obstacles), _near_parallel(pts, lines), i, kind, pts)
              for i, (kind, pts) in enumerate(cands)]
    *_, kind, pts = min(scored)
    return kind, pts


def apply_spec(base: rdm.Model, spec: dict) -> tuple[rdm.Model, list[str]]:
    model = base.clone()
    ed = Editor(model)
    for r in spec.get("dropReferences", []):
        ed.drop_reference(r)
    for t in spec.get("dropTables", []):
        ed.drop_table(t)
    for t in spec.get("createTables", []):
        ed.create_table(t)
    for t in spec.get("alterTables", []):
        ed.alter_table(t)
    for r in spec.get("addReferences", []):
        ed.add_reference(r)
    # resize displays of created tables now that FK flags are known
    for name in ed.created_tables:
        t = model.table(name)
        w, h = ed._size_for(t)
        for td in model.table_displays(t.get("Id")):
            td.find("Width").text = _fmt(w)
            td.find("Height").text = _fmt(h)
    return model, ed.warnings


# =========================================================================== SQL

def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def column_sql(c: rdm.Column) -> str:
    s = f"{q(c.name)} {c.type}  {'NULL' if c.nullable else 'NOT NULL'}"
    if c.default:
        s += f" DEFAULT {c.default}"
    if c.check:
        s += f" CHECK ({c.check})"
    return s


def index_sql(t: rdm.Table, ix: dict) -> str:
    cols = ",".join(f"{q(t.col(cid).name)} {'ASC' if asc else 'DESC'}" for cid, asc in ix["columns"])
    s = f"-- Index: {ix['name']}.\nCREATE {'UNIQUE ' if ix['unique'] else ''}INDEX {q(ix['name'])} on {t.qualified} ({cols})"
    if ix.get("where"):
        s += f"\n    WHERE {ix['where']}"
    return s + ";"


def check_sql(t: rdm.Table, ck: dict) -> str:
    return (f"-- Check: {ck['name']} [Table: {t.name}.]\nALTER TABLE {t.qualified}\n"
            f"    ADD CONSTRAINT {q(ck['name'])} CHECK ({ck['expression']}) NOT DEFERRABLE INITIALLY IMMEDIATE;")


def uk_sql(t: rdm.Table, ak: dict) -> str:
    cols = ",".join(q(t.col(c).name) for c in ak["columns"])
    return (f"-- Unique key: {ak['name']} [Table: {t.name}.]\nALTER TABLE {t.qualified}\n"
            f"    ADD CONSTRAINT {q(ak['name'])} UNIQUE ({cols}) NOT DEFERRABLE INITIALLY IMMEDIATE;")


def create_table_sql(t: rdm.Table) -> str:
    lines = [column_sql(c) for c in t.columns]
    for ck in t.checks:
        lines.append(f"CONSTRAINT {q(ck['name'])} CHECK ({ck['expression']}) NOT DEFERRABLE INITIALLY IMMEDIATE")
    for ak in t.alternate_keys:
        cols = ",".join(q(t.col(c).name) for c in ak["columns"])
        lines.append(f"CONSTRAINT {q(ak['name'])} UNIQUE ({cols}) NOT DEFERRABLE INITIALLY IMMEDIATE")
    if t.pk:
        lines.append(f"CONSTRAINT {q(t.pk_name or t.name + '_pk')} PRIMARY KEY ({','.join(q(t.col(c).name) for c in t.pk)})")
    body = ",\n".join("    " + ln for ln in lines)
    return f"-- Table: {t.name}.\nCREATE TABLE {t.qualified} (\n{body}\n);"


def reference_sql(r: rdm.Reference, snap: dict) -> str:
    pk = snap["by_id"][r.pk_table]
    fk = snap["by_id"][r.fk_table]
    fcols = ", ".join(q(fk.col(f).name) for _, f in r.pairs)
    pcols = ", ".join(q(pk.col(p).name) for p, _ in r.pairs)
    s = (f"-- Reference: {r.name} [Table: {fk.name}.]\nALTER TABLE {fk.qualified} ADD CONSTRAINT {q(r.name)}\n"
         f"    FOREIGN KEY ({fcols})\n    REFERENCES {pk.qualified} ({pcols})  \n")
    if r.on_delete in ON_ACTION_SQL:
        s += f"    ON DELETE  {ON_ACTION_SQL[r.on_delete]}  \n"
    if r.on_update in ON_ACTION_SQL:
        s += f"    ON UPDATE  {ON_ACTION_SQL[r.on_update]}  \n"
    return s + "    NOT DEFERRABLE \n    INITIALLY IMMEDIATE\n;"


def drop_reference_sql(r: rdm.Reference, snap: dict) -> str:
    fk = snap["by_id"][r.fk_table]
    return f"-- Reference: {r.name} [Table: {fk.name}.]\nALTER TABLE {fk.qualified} DROP CONSTRAINT {q(r.name)};"


def diff_snapshots(old: dict, new: dict) -> dict:
    """Structural difference between two snapshots, keyed by names."""
    ot, nt = old["tables"], new["tables"]
    d = {"created": [n for n in nt if n not in ot], "dropped": [n for n in ot if n not in nt], "altered": {}}
    for n in nt:
        if n not in ot:
            continue
        a, b = ot[n], nt[n]
        # columns are matched by model id, so a rename is a rename and not a drop plus an add
        ac = {c.id: c for c in a.columns}
        bc = {c.id: c for c in b.columns}
        changes = {
            "added_columns": [c for c in b.columns if c.id not in ac],
            "dropped_columns": [c for c in a.columns if c.id not in bc],
            "renamed_columns": [(ac[c.id], c) for c in b.columns if c.id in ac and ac[c.id].name != c.name],
            "altered_columns": [(ac[c.id], c) for c in b.columns if c.id in ac and
                                (ac[c.id].type, ac[c.id].nullable, ac[c.id].default, ac[c.id].check) !=
                                (c.type, c.nullable, c.default, c.check)],
        }
        aix = {i["name"]: i for i in a.indexes}
        bix = {i["name"]: i for i in b.indexes}
        changes["added_indexes"] = [i for k, i in bix.items() if k not in aix]
        changes["dropped_indexes"] = [i for k, i in aix.items() if k not in bix]
        ack = {i["name"]: i for i in a.checks}
        bck = {i["name"]: i for i in b.checks}
        changes["added_checks"] = [i for k, i in bck.items() if k not in ack]
        changes["dropped_checks"] = [i for k, i in ack.items() if k not in bck]
        aak = {i["name"]: i for i in a.alternate_keys}
        bak = {i["name"]: i for i in b.alternate_keys}
        changes["added_uks"] = [i for k, i in bak.items() if k not in aak]
        changes["dropped_uks"] = [i for k, i in aak.items() if k not in bak]
        if any(changes.values()):
            d["altered"][n] = changes
    orf, nrf = old["references"], new["references"]
    d["added_references"] = [r for k, r in nrf.items() if k not in orf]
    d["dropped_references"] = [r for k, r in orf.items() if k not in nrf]
    return d


def alter_column_sql(t: rdm.Table, old: rdm.Column, new: rdm.Column) -> list[str]:
    out = []
    head = f"-- Column: {t.name}.{new.name}.\nALTER TABLE {t.qualified}\n    "
    if old.type != new.type:
        out.append(head + f"ALTER {q(new.name)} TYPE {new.type};  -- check that existing data converts")
    if old.nullable != new.nullable:
        out.append(head + f"ALTER {q(new.name)} {'DROP' if new.nullable else 'SET'} NOT NULL;")
    if old.default != new.default:
        out.append(head + (f"ALTER {q(new.name)} SET DEFAULT {new.default};" if new.default
                           else f"ALTER {q(new.name)} DROP DEFAULT;"))
    if old.check != new.check:
        cname = f"{t.name}_{new.name}_check"
        s = head + f"DROP CONSTRAINT IF EXISTS {q(cname)};  -- PostgreSQL's default name for an inline check"
        if new.check:
            s += f"\nALTER TABLE {t.qualified}\n    ADD CONSTRAINT {q(cname)} CHECK ({new.check});"
        out.append(s)
    return out


def generate_sql(base: rdm.Model, new: rdm.Model, spec: dict, spec_label: str) -> tuple[str, str]:
    old_s = rdm.schema_snapshot(base)
    new_s = rdm.schema_snapshot(new)
    d = diff_snapshots(old_s, new_s)
    name = spec.get("name", "Migration")
    today = dt.date.today().isoformat()
    head = (f"-- Migration: {name}\n-- Date: {today}\n"
            f"-- Generated by schema-migrations from {spec_label}: edit the spec and regenerate rather than this file.\n")

    data = spec.get("data") or {}
    # Up: loosen (drop references, checks, unique keys), add structure, migrate data, then remove structure,
    # so that data.up can still read the columns and tables the migration drops.
    up: list[str] = []
    if data.get("before"):
        up.append("-- Data migration (before the structure changes).\n" + data["before"].strip())
    for r in d["dropped_references"]:
        up.append(drop_reference_sql(r, old_s))
    for tn, ch in d["altered"].items():
        t_old = old_s["tables"][tn]
        for ck in ch["dropped_checks"]:
            up.append(f"-- Check: {ck['name']} [Table: {tn}.]\nALTER TABLE {t_old.qualified} DROP CONSTRAINT {q(ck['name'])};")
        for ak in ch["dropped_uks"]:
            up.append(f"-- Unique key: {ak['name']} [Table: {tn}.]\nALTER TABLE {t_old.qualified} DROP CONSTRAINT {q(ak['name'])};")
    for tn in d["created"]:
        up.append(create_table_sql(new_s["tables"][tn]))
    for tn, ch in d["altered"].items():
        t = new_s["tables"][tn]
        for o, n in ch["renamed_columns"]:
            up.append(f"-- Column: {tn}.{o.name} -> {n.name}.\nALTER TABLE {t.qualified}\n    RENAME COLUMN {q(o.name)} TO {q(n.name)};")
        for c in ch["added_columns"]:
            if not c.nullable and not c.default:
                up.append(f"-- WARNING: {tn}.{c.name} is NOT NULL without default: fails if the table has rows.")
            up.append(f"-- Column: {tn}.{c.name}.\nALTER TABLE {t.qualified}\n    ADD {column_sql(c)};")
        for old_c, new_c in ch["altered_columns"]:
            up.extend(alter_column_sql(t, old_c, new_c))
    for tn in d["created"]:
        t = new_s["tables"][tn]
        up.extend(index_sql(t, ix) for ix in t.indexes)
    for tn, ch in d["altered"].items():
        t = new_s["tables"][tn]
        up.extend(index_sql(t, ix) for ix in ch["added_indexes"])
        up.extend(check_sql(t, ck) for ck in ch["added_checks"])
        up.extend(uk_sql(t, ak) for ak in ch["added_uks"])
    for r in d["added_references"]:
        up.append(reference_sql(r, new_s))
    if data.get("up"):
        up.append("-- Data migration.\n" + data["up"].strip())
    for tn, ch in d["altered"].items():
        t_old = old_s["tables"][tn]
        for ix in ch["dropped_indexes"]:
            up.append(f"-- Index: {ix['name']}.\nDROP INDEX {q(ix['name'])};")
        for c in ch["dropped_columns"]:
            up.append(f"-- Column: {tn}.{c.name}.\nALTER TABLE {t_old.qualified}\n    DROP COLUMN {q(c.name)};")
    for tn in d["dropped"]:
        up.append(f"-- Table: {tn}.\nDROP TABLE {old_s['tables'][tn].qualified};")

    # Down mirrors Up: restore what Up removed, reverse the data, remove what Up added, restore constraints.
    down: list[str] = []
    for tn in d["dropped"]:
        t = old_s["tables"][tn]
        down.append("-- Restores the structure only: the rows deleted by Up are lost.\n" + create_table_sql(t))
        down.extend(index_sql(t, ix) for ix in t.indexes)
    for tn, ch in d["altered"].items():
        t_old = old_s["tables"][tn]
        for c in ch["dropped_columns"]:
            col = c
            note = ""
            if not c.nullable and not c.default:
                col = rdm.Column(c.id, c.name, c.type, True, c.default, c.check, c.description)
                note = "-- Re-added as NULL: its values were dropped by Up.\n"
            down.append(f"{note}-- Column: {tn}.{c.name}.\nALTER TABLE {t_old.qualified}\n    ADD {column_sql(col)};")
        down.extend(index_sql(t_old, ix) for ix in ch["dropped_indexes"])
    if data.get("down"):
        down.append("-- Data migration (reverse).\n" + data["down"].strip())
    for r in reversed(d["added_references"]):
        down.append(drop_reference_sql(r, new_s))
    for tn, ch in d["altered"].items():
        t = new_s["tables"][tn]
        for ak in ch["added_uks"]:
            down.append(f"-- Unique key: {ak['name']} [Table: {tn}.]\nALTER TABLE {t.qualified} DROP CONSTRAINT {q(ak['name'])};")
        for ck in ch["added_checks"]:
            down.append(f"-- Check: {ck['name']} [Table: {tn}.]\nALTER TABLE {t.qualified} DROP CONSTRAINT {q(ck['name'])};")
        for ix in ch["added_indexes"]:
            down.append(f"-- Index: {ix['name']}.\nDROP INDEX {q(ix['name'])};")
        for o, n in ch["renamed_columns"]:
            down.append(f"-- Column: {tn}.{n.name} -> {o.name}.\nALTER TABLE {t.qualified}\n    RENAME COLUMN {q(n.name)} TO {q(o.name)};")
        for old_c, new_c in ch["altered_columns"]:
            down.extend(alter_column_sql(t, new_c, old_c))
        for c in reversed(ch["added_columns"]):
            down.append(f"-- Column: {tn}.{c.name}.\nALTER TABLE {t.qualified}\n    DROP COLUMN {q(c.name)};")
    for tn in reversed(d["created"]):
        down.append(f"-- Table: {tn}.\nDROP TABLE {new_s['tables'][tn].qualified};")
    for tn, ch in d["altered"].items():
        t_old = old_s["tables"][tn]
        down.extend(check_sql(t_old, ck) for ck in ch["dropped_checks"])
        down.extend(uk_sql(t_old, ak) for ak in ch["dropped_uks"])
    for r in d["dropped_references"]:
        down.append(reference_sql(r, old_s))
    if data.get("beforeDown"):
        down.append("-- Data migration (reverse of data.before).\n" + data["beforeDown"].strip())

    up_sql = head + "\n\n" + "\n\n".join(up) + "\n"
    down_sql = head.replace("-- Migration:", "-- Rollback of migration:") + "\n\n" + "\n\n".join(down) + "\n"
    return up_sql, down_sql


# =========================================================================== CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="base model (left untouched)")
    ap.add_argument("--spec", help="JSON change spec")
    ap.add_argument("--out", required=True, help="where to write the resulting model")
    ap.add_argument("--up", help="where to write the Up SQL")
    ap.add_argument("--down", help="where to write the Down SQL")
    ap.add_argument("--init", action="store_true", help="write an empty model to --out and stop")
    ap.add_argument("--name", default="", help="model name for --init")
    args = ap.parse_args(argv)

    if args.init:
        rdm.Model.empty(args.name).save(args.out)
        print(f"empty model written to {args.out}")
        return 0
    if not args.model or not args.spec:
        ap.error("--model and --spec are required")
    base = rdm.Model.load(args.model)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    try:
        new, warnings = apply_spec(base, spec)
    except SpecError as e:
        print(f"spec error: {e}", file=sys.stderr)
        return 2
    new.save(args.out)
    counts = Counter(el.get("Id") for el in new.root.iter() if el.get("Id"))
    dupes = {i for i, n in counts.items() if n > 1}
    if dupes:
        print(f"duplicate ids in result: {sorted(dupes)}", file=sys.stderr)
        return 3
    if args.up or args.down:
        up, down = generate_sql(base, new, spec, Path(args.spec).name)
        if args.up:
            Path(args.up).write_text(up, encoding="utf-8")
        if args.down:
            Path(args.down).write_text(down, encoding="utf-8")
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    print(f"model written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
