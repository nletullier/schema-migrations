#!/usr/bin/env python3
"""Draw a Redgate Data Modeler XML model as SVG, in the style of Redgate's own export.

    # the whole model, no colours
    python3 -I render_svg.py --model model.xml --out schema.svg

    # the whole model again, with what a migration changes coloured in: added in green,
    # removed in red (struck through), modified in amber
    python3 -I render_svg.py --model new.xml --base old.xml --diff --out diff.svg --title "AddInvoices"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rdm  # noqa: E402
from apply_changes import diff_snapshots  # noqa: E402

BLACK = "#000000"
ADD, ADD_FILL = "#1a7f37", "#dafbe1"
DEL, DEL_FILL = "#cf222e", "#ffebe9"
MOD, MOD_FILL = "#9a6700", "#fff8c5"
HEADER_FILL = "#f0f0f0"
FONT = "arial, sans-serif"
ROW_H, HEADER_H, TOP_PAD = 14.0, 20.0, 3.0
MARGIN = 20.0


# --------------------------------------------------------------------------- geometry helpers

def _inside(p, r, eps=0.01):
    x, y, w, h = r
    return x - eps <= p[0] <= x + w + eps and y - eps <= p[1] <= y + h + eps


def _clip_entry(a, b, r):
    """Point where segment a→b first meets the border of rect r (a outside, b inside)."""
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        p = (a[0] + (b[0] - a[0]) * mid, a[1] + (b[1] - a[1]) * mid)
        if _inside(p, r):
            hi = mid
        else:
            lo = mid
    return (a[0] + (b[0] - a[0]) * hi, a[1] + (b[1] - a[1]) * hi)


def _border_point(points, rect, at_end):
    """Where the polyline enters (at_end) or leaves (not at_end) the given table rectangle."""
    pts = list(points)
    if not at_end:
        pts = pts[::-1]
    # walk from the far side towards the table
    for i in range(len(pts) - 1, 0, -1):
        a, b = pts[i - 1], pts[i]
        if not _inside(a, rect) and _inside(b, rect):
            p = _clip_entry(a, b, rect)
            return p, _unit(a, b)
    a, b = pts[-2], pts[-1]
    return b, _unit(a, b)


def _unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = (dx * dx + dy * dy) ** 0.5 or 1.0
    return dx / n, dy / n


# --------------------------------------------------------------------------- renderer

class Renderer:
    def __init__(self, model: rdm.Model, base: rdm.Model | None = None, diff: bool = False, title: str = ""):
        self.m = model
        self.base = base
        self.diff = diff and base is not None
        self.title = title
        self.snap = rdm.schema_snapshot(model)
        self.base_snap = rdm.schema_snapshot(base) if base is not None else None
        self.d = diff_snapshots(self.base_snap, self.snap) if self.diff else None
        self.parts_refs: list[str] = []
        self.parts_tables: list[str] = []
        self.parts_areas: list[str] = []
        self.bounds: list[tuple[float, float, float, float]] = []

    # -- selection ---------------------------------------------------------
    def _fk_ids(self, snap) -> set[str]:
        return {f for r in snap["references"].values() for _, f in r.pairs}

    def render(self) -> str:
        if self.diff:
            self._render_diff()
        else:
            self._render_full()
        return self._assemble()

    def _render_full(self):
        fk = self._fk_ids(self.snap)
        displays = {td.get("Id"): td for td in self.m.table_displays()}
        for ar in self.m.section("Areas"):
            self._area(ar)
        for rd in self.m.reference_displays():
            ref = self.m.root.find(f"./References/Reference[@Id='{rd.findtext('Reference')}']")
            if ref is None:
                continue
            self._reference(rd, rdm.reference_view(ref), displays, BLACK, self.snap)
        for td in displays.values():
            t = self.snap["by_id"].get(td.findtext("Table"))
            if t is not None:
                self._table(td, [(c, None) for c in t.columns], t, fk, state=None)

    def _render_diff(self):
        """The whole model, as in the full view, with what the migration changes coloured in."""
        d = self.d
        snap, bsnap = self.snap, self.base_snap
        new_fk, old_fk = self._fk_ids(snap), self._fk_ids(bsnap)
        added_ref_ids = {r.id for r in d["added_references"]}
        dropped_ref_ids = {r.id for r in d["dropped_references"]}
        new_displays = {td.get("Id"): td for td in self.m.table_displays()}
        old_displays = {td.get("Id"): td for td in self.base.table_displays()}
        both = dict(old_displays)
        both.update(new_displays)

        for ar in self.m.section("Areas"):
            self._area(ar)
        # unchanged references first, so the coloured ones are drawn on top
        coloured = []
        for rd in self.m.reference_displays():
            rid = rd.findtext("Reference")
            ref = self.m.root.find(f"./References/Reference[@Id='{rid}']")
            if ref is None:
                continue
            if rid in added_ref_ids:
                coloured.append((rd, rdm.reference_view(ref), new_displays, ADD, snap, False))
            else:
                self._reference(rd, rdm.reference_view(ref), new_displays, BLACK, snap)
        for rd in self.base.reference_displays():
            rid = rd.findtext("Reference")
            if rid not in dropped_ref_ids:
                continue
            ref = self.base.root.find(f"./References/Reference[@Id='{rid}']")
            coloured.append((rd, rdm.reference_view(ref), both, DEL, bsnap, True))
        for rd, ref, displays, color, sn, dashed in coloured:
            self._reference(rd, ref, displays, color, sn, width=1.8, dashed=dashed)

        for td in new_displays.values():
            t = snap["by_id"].get(td.findtext("Table"))
            if t is None:
                continue
            if t.name in d["created"]:
                self._table(td, [(c, "added") for c in t.columns], t, new_fk, state="added")
            elif t.name in d["altered"]:
                ch = d["altered"][t.name]
                added = {c.name for c in ch["added_columns"]}
                modified = {n.name for _, n in ch["altered_columns"] + ch["renamed_columns"]}
                rows = [(c, "added" if c.name in added else "modified" if c.name in modified else None)
                        for c in t.columns]
                rows += [(c, "dropped") for c in ch["dropped_columns"]]
                self._table(td, rows, t, new_fk | old_fk, state="altered")
            else:
                self._table(td, [(c, None) for c in t.columns], t, new_fk, state=None)
        for name in d["dropped"]:
            t = bsnap["tables"][name]
            for td in self.base.table_displays(t.id):
                self._table(td, [(c, "dropped") for c in t.columns], t, old_fk, state="dropped")

    # -- primitives --------------------------------------------------------
    def _area(self, ar):
        di = ar.find("DisplayInfo")
        if di is None:
            return
        x, y, w, h = (float(di.findtext(k)) for k in ("X", "Y", "Width", "Height"))
        dash = (di.findtext("DashArray") or "").strip("[]")
        nx = x + float(di.findtext("NameX") or 0)
        ny = y + float(di.findtext("NameY") or 0)
        self.parts_areas.append(
            f'<g class="area"><rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{di.findtext("FillColor") or "#F8F8F8"}" '
            f'stroke="{di.findtext("LineColor") or BLACK}"' + (f' stroke-dasharray="{dash}"' if dash else "") + "/>"
            f'<text x="{nx}" y="{ny + 14}" font-family="{FONT}" font-size="15" font-weight="700">'
            f'{escape(ar.findtext("Name") or "")}</text></g>')
        self.bounds.append((x, y, w, h))

    def _reference(self, rd, ref: rdm.Reference, displays, color, snap, width=1.0, dashed=False):
        pts = [(float(cp.findtext("X")), float(cp.findtext("Y"))) for cp in rd.findall("./ControlPoints/ControlPoint")]
        if len(pts) < 2:
            return
        pk_td = displays.get(rd.findtext("PkTableDisplay"))
        fk_td = displays.get(rd.findtext("FkTableDisplay"))
        out = [f'<g class="reference"><polyline fill="none" stroke="{color}" stroke-width="{width}" '
               f'stroke-linejoin="miter" points="{" ".join(f"{x},{y}" for x, y in pts)}"'
               + (' stroke-dasharray="6,4"' if dashed else "") + "/>"]
        if fk_td is not None:
            e, (ux, uy) = _border_point(pts, rdm.display_rect(fk_td), at_end=True)
            apex = (e[0] - ux * 15, e[1] - uy * 15)
            px, py = -uy, ux
            for s in (1, -1):
                out.append(f'<line x1="{apex[0]:.1f}" y1="{apex[1]:.1f}" x2="{e[0] + px * 6 * s:.1f}" '
                           f'y2="{e[1] + py * 6 * s:.1f}" stroke="{color}" stroke-width="{width}"/>')
            out.append(f'<circle cx="{apex[0]:.1f}" cy="{apex[1]:.1f}" r="3.5" fill="white" stroke="{color}"/>')
        if pk_td is not None:
            e, (ux, uy) = _border_point(pts, rdm.display_rect(pk_td), at_end=False)
            # direction pointing away from the PK table
            ux, uy = -ux, -uy
            m = (e[0] + ux * 15, e[1] + uy * 15)
            if ref.mandatory:
                out.append(f'<line x1="{m[0] - uy * 6:.1f}" y1="{m[1] + ux * 6:.1f}" x2="{m[0] + uy * 6:.1f}" '
                           f'y2="{m[1] - ux * 6:.1f}" stroke="{color}" stroke-width="{width}"/>')
            else:
                out.append(f'<circle cx="{m[0]:.1f}" cy="{m[1]:.1f}" r="3.5" fill="white" stroke="{color}"/>')
        out.append("</g>")
        self.parts_refs.append("".join(out))
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self.bounds.append((min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))

    def _table(self, td, rows, t: rdm.Table, fk_ids, state):
        x, y, w, h = rdm.display_rect(td)
        h = max(h, HEADER_H + TOP_PAD + ROW_H * len(rows) + 8)
        fixed = td.findtext("FixedSize") == "true"
        border, head_fill, title_col, text_col = BLACK, HEADER_FILL, BLACK, BLACK
        if state == "added":
            border, head_fill, title_col = ADD, ADD_FILL, ADD
        elif state == "dropped":
            border, head_fill, title_col, text_col = DEL, DEL_FILL, DEL, DEL
        elif state == "altered":
            border = MOD
        flags = [rdm.column_flags(t, c, fk_ids) for c, _ in rows]
        bold = [cs in ("added", "dropped", "modified") for _, cs in rows]
        flag_w = max([rdm.text_width(f, bold=b) for f, b in zip(flags, bold)] + [rdm.text_width("PK")])
        type_w = max([rdm.text_width(c.type, bold=b) for (c, _), b in zip(rows, bold)] + [20])
        flag_x = w - flag_w - 6
        type_x = max(flag_x - type_w - 12, w * 0.38)
        name_max = type_x - 10
        type_max = flag_x - type_x - 6
        o = [f'<g class="table" transform="translate({x},{y})">',
             f'<rect width="{w}" height="{HEADER_H}" fill="{head_fill}"/>',
             f'<text x="{w / 2:.1f}" y="14.19" text-anchor="middle" font-family="{FONT}" font-size="12" '
             f'font-weight="700" fill="{title_col}"'
             + (' text-decoration="line-through"' if state == "dropped" else "") + f'>{escape(t.name)}</text>',
             f'<rect y="{HEADER_H}" width="{w}" height="{h - HEADER_H}" fill="#ffffff"/>']
        for i, ((c, cstate), fl) in enumerate(zip(rows, flags)):
            ry = HEADER_H + TOP_PAD + i * ROW_H
            col = text_col
            deco = ""
            if cstate == "added":
                col = ADD
                o.append(f'<rect x="1" y="{ry}" width="{w - 2}" height="{ROW_H}" fill="{ADD_FILL}"/>')
            elif cstate == "dropped":
                col = DEL
                deco = ' text-decoration="line-through"'
                o.append(f'<rect x="1" y="{ry}" width="{w - 2}" height="{ROW_H}" fill="{DEL_FILL}"/>')
            elif cstate == "modified":
                col = MOD
                o.append(f'<rect x="1" y="{ry}" width="{w - 2}" height="{ROW_H}" fill="{MOD_FILL}"/>')
            weight = "700" if cstate in ("added", "dropped", "modified") else "400"
            for tx, txt, maxw in ((4, c.name, name_max), (type_x, c.type, type_max), (flag_x, fl, flag_w + 4)):
                o.append(f'<text x="{tx + 1:.2f}" y="{ry + 11.14:.2f}" font-family="{FONT}" font-size="12" '
                         f'font-weight="{weight}" fill="{col}"{deco}>{escape(_fit(txt, maxw))}</text>')
        dash = ' stroke-dasharray="2, 2"' if fixed else ""
        sw = "1.6" if state in ("added", "dropped", "altered") else "1"
        o.append(f'<polyline fill="none" stroke="{border}" stroke-width="{sw}" stroke-linejoin="miter"{dash} '
                 f'points="0,{HEADER_H} 0,0 {w},0 {w},{HEADER_H} 0,{HEADER_H} 0,{h} {w},{h} {w},{HEADER_H}"/>')
        o.append("</g>")
        self.parts_tables.append("".join(o))
        self.bounds.append((x, y, w, h))

    # -- output ------------------------------------------------------------
    def _assemble(self) -> str:
        if not self.bounds:
            self.bounds.append((0, 0, 200, 100))
        min_x = min(b[0] for b in self.bounds) - MARGIN
        min_y = min(b[1] for b in self.bounds) - MARGIN
        max_x = max(b[0] + b[2] for b in self.bounds) + MARGIN
        max_y = max(b[1] + b[3] for b in self.bounds) + MARGIN
        legend = ""
        if self.diff:
            # sized for the whole drawing, which is usually shown scaled down to the page width
            k = max(1.0, (max_x - min_x) / 1600)
            legend_h = 70 * k
            min_y -= legend_h + MARGIN
            lx, ly = min_x + MARGIN, min_y + MARGIN
            items = [(ADD, ADD_FILL, "added"), (DEL, DEL_FILL, "removed"), (MOD, MOD_FILL, "modified")]
            legend = (f'<g class="legend"><text x="{lx:.1f}" y="{ly + 18 * k:.1f}" font-family="{FONT}" '
                      f'font-size="{20 * k:.1f}" font-weight="700">{escape(self.title or "Schema changes")}</text>')
            for i, (c, f, label) in enumerate(items):
                ix = lx + i * 160 * k
                legend += (f'<rect x="{ix:.1f}" y="{ly + 36 * k:.1f}" width="{20 * k:.1f}" height="{15 * k:.1f}" '
                           f'fill="{f}" stroke="{c}" stroke-width="{1.5 * k:.1f}"/>'
                           f'<text x="{ix + 28 * k:.1f}" y="{ly + 49 * k:.1f}" font-family="{FONT}" '
                           f'font-size="{15 * k:.1f}" fill="{c}">{label}</text>')
            legend += "</g>"
            # wide enough for the legend and the whole title
            title_w = rdm.text_width(self.title or "Schema changes", size=20 * k, bold=True)
            max_x = max(max_x, min_x + 720, lx + title_w + MARGIN * 2)
        width, height = max_x - min_x, max_y - min_y
        body = "".join(self.parts_areas) + "".join(self.parts_refs) + "".join(self.parts_tables) + legend
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.1f}" height="{height:.1f}" '
                f'viewBox="{min_x:.1f} {min_y:.1f} {width:.1f} {height:.1f}">'
                f'<desc>{escape(self.title)}</desc>'
                f'<rect x="{min_x:.1f}" y="{min_y:.1f}" width="{width:.1f}" height="{height:.1f}" fill="#ffffff"/>'
                f'{body}</svg>\n')


def _fit(s: str, maxw: float) -> str:
    if rdm.text_width(s) <= maxw:
        return s
    while s and rdm.text_width(s) > maxw:
        s = s[:-1]
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--base")
    ap.add_argument("--diff", action="store_true")
    ap.add_argument("--title", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.diff and not args.base:
        ap.error("--diff needs --base")
    model = rdm.Model.load(args.model)
    base = rdm.Model.load(args.base) if args.base else None
    svg = Renderer(model, base, args.diff, args.title).render()
    Path(args.out).write_text(svg, encoding="utf-8")
    print(f"svg written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
