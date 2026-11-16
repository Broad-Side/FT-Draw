#!/usr/bin/env python3
"""
Build a Draw.io wiring drawing from a project points.yml (schema_version 4).

How the drawing is laid out
---------------------------
Every point in points.yml becomes one "block", drawn in the order it appears
in the file. Each block is placed underneath the previous one.

Inside a block everything sits on rows that are `row_height` apart
(default 32):

    controller side   | terminal columns ...               | field device
    ------------------+------------------------------------+-------------
    row 0  UI1    [1] | ---- connection 1 (first wiring) ---| [SIG]
    row 1  C 1/2  [2] | ---- connection 2 ------------------| [+]
    row 2             | ---- connection 3 ------------------|

  * Controller rows come from the controller template YAML (one row per
    interface on that point, in the order the template lists them), so any
    BMS/BAS controller works as long as it has a template.
  * Field device rows come from field_device.interfaces, sorted by `order`.
  * Every entry under `wiring:` gets its own row, in the order written.

The block is as tall as whichever of those three lists is longest, and the
next point starts below it (never closer than layout.point_pitch_y).

Terminals are placed in columns using the terminal block `order`
(layout.terminal_block_info.terminal_blocks_order; X3-001 -> block X3 -> its
column). A terminal that is used by more than one connection is only drawn
once; later connections wire back to it.

After the points page, one extra page is added per terminal block that is
used (see render_strips), built from layout.terminal_block_info.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import xml.etree.ElementTree as ET

import yaml

# --------------------------------------------------------------------------
# Drawing constants
# --------------------------------------------------------------------------
FONT = "fontFamily=Courier New;"
BOX = "rounded=0;whiteSpace=wrap;html=1;" + FONT

NAME_W, NAME_H = 207, 48          # equipment / tag / description text box
TYPE_W = 60                       # "UI1", "C 1/2" boxes
CONN_W = 20                       # controller terminal number box
TERM_W, TERM_H = 60, 12           # panel terminal box
FIELD_W = 30                      # field interface box ("SIG", "+")
LABEL_W, LABEL_H = 58, 14         # wire number label

EDGE_STYLE = (
    "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;"
    "html=1;endArrow=none;strokeWidth=2;jumpStyle=arc;"
    "exitX=1;exitY=0.5;exitDx=0;exitDy=0;entryX=0;entryY=0.5;entryDx=0;entryDy=0;"
)


class ConfigError(Exception):
    """A problem in the YAML that the user needs to fix."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def safe_id(*parts) -> str:
    """Readable, stable Draw.io id, e.g. SNC01_UI1_signal."""
    return re.sub(r"[^A-Za-z0-9_]+", "_", "_".join(map(str, parts))).strip("_")


def hash_id(prefix: str, *parts) -> str:
    return f"{prefix}_{hashlib.sha1('|'.join(map(str, parts)).encode()).hexdigest()[:10]}"


def num(v: float) -> str:
    v = float(v)
    return str(int(v)) if v.is_integer() else str(v)


def short_name(interface: str) -> str:
    """Fallback box text when the controller template has no display_name:
    'output_common' -> 'OUT COM', 'supply' -> 'SUP'."""
    return " ".join(w[:3] for w in str(interface).upper().split("_") if w)


TERMINAL_RE = re.compile(r"^(?P<block>[A-Z0-9]+)-(?P<num>\d+)(?P<level>[A-Z]?)$")
LEVELS = {"single": "A", "fuse": "A", "double": "AB", "triple": "ABC"}
COLOURS = {  # terminal block colour -> (fill, font)
    "grey": ("#D9D9D9", "#000000"), "gray": ("#D9D9D9", "#000000"),
    "red": ("#E51400", "#FFFFFF"), "black": ("#000000", "#FFFFFF"),
    "blue": ("#1BA1E2", "#FFFFFF"), "white": ("#FFFFFF", "#000000"),
    "yellow": ("#FFE600", "#000000"), "green": ("#60A917", "#FFFFFF"),
    "orange": ("#FA6800", "#000000"), "brown": ("#A0522D", "#FFFFFF"),
}
COMMON_STROKE = "strokeWidth=3;"


def terminal_block(terminal: str) -> str:
    """'X8-001B' -> 'X8'. Anything without a dash belongs to OTHER."""
    t = str(terminal)
    return t.split("-", 1)[0].upper() if "-" in t else "OTHER"


# --------------------------------------------------------------------------
# Controller templates
# --------------------------------------------------------------------------
def find_controller_template(name: str, search_dirs: list[Path]) -> Path:
    """Find <name>.yml in the search dirs. Matches by file stem, case-insensitive,
    and also accepts a vendor prefix (JCI_SNC25152.yml matches SNC25152)."""
    wanted = str(name).lower()
    candidates = []
    for d in search_dirs:
        if d.is_dir():
            candidates += sorted(d.rglob("*.yml")) + sorted(d.rglob("*.yaml"))
    for p in candidates:
        if p.stem.lower() == wanted:
            return p
    for p in candidates:
        if p.stem.lower().endswith("_" + wanted) or wanted.endswith("_" + p.stem.lower()):
            return p
    raise ConfigError(
        f"controller template {name!r} not found in: " + ", ".join(str(d) for d in search_dirs)
    )


def load_controller_points(path: Path) -> dict:
    data = load_yaml(path)
    pts = data.get("points") or (data.get("controller") or {}).get("points")
    if not isinstance(pts, dict) or not pts:
        raise ConfigError(f"{path}: no 'points' found in controller template")
    return pts


# --------------------------------------------------------------------------
# Draw.io document
# --------------------------------------------------------------------------
@dataclass
class Box:
    id: str
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w


def _plain(label: str) -> str:
    """Visible text of a draw.io HTML label."""
    return html.unescape(re.sub(r"<[^>]+>", "", label or "")).replace("\xa0", " ").strip()


TEXT_SUFFIX = "_text"   # Client -> the box with id Client_text holds the value

# revHistory.csv column -> background cell id of that column in the history row
REV_HISTORY_IDS = {
    "Rev": "Rev_Number_History",
    "Date": "Rev_Date_History",
    "Description": "Rev_Description_History",
    "Reviewer": "Rev_Checked_History",
}


def fill_title_block(elements: list, fields: dict) -> list[str]:
    """Write each field into the background box with id <field>_text, e.g.
    projectDetails.yml "Client: MSFT" -> the box Client_text. The value
    replaces the box's placeholder text, so its font size and bold are kept.
    Returns the fields that had no such box."""
    by_id = {el.get("id"): el for el in elements}
    missing = []
    for key, value in fields.items():
        target = by_id.get(key + TEXT_SUFFIX)
        if target is None:
            missing.append(key)
        elif value is not None:
            set_text(target, value)
    return missing


def set_text(el, value) -> None:
    """Replace a cell's visible text, keeping its font tags (size, bold)."""
    attr = "label" if el.tag == "object" else "value"
    old = el.get(attr) or ""
    new = html.escape(str(value), quote=False)
    if _plain(old):
        parts = re.split(r"(<[^>]*>)", old)
        i = next(i for i, p in enumerate(parts) if not p.startswith("<") and p.strip())
        parts[i] = new
        el.set(attr, "".join(parts))
    else:
        el.set(attr, new)


def _geo(el):
    cell = el if el.tag == "mxCell" else el.find("mxCell")
    return cell.find("mxGeometry") if cell is not None else None


def fill_rev_history(elements: list, revisions: list) -> tuple[list, list[str]]:
    """Draw one revision history row per revision, newest at the top.

    The template row is the cells named in REV_HISTORY_IDS. The oldest
    revision goes in that row; each newer one is a copy drawn one row higher
    (row height = Rev_Number_History's height), so the list grows up the page.
    Returns the new element list and any REV_HISTORY_IDS cells not found."""
    by_id = {el.get("id"): el for el in elements}
    missing = [i for i in REV_HISTORY_IDS.values() if i not in by_id]
    row = {col: by_id[i] for col, i in REV_HISTORY_IDS.items() if i in by_id}
    if not row or not revisions:
        return elements, missing
    first = row.get("Rev", next(iter(row.values())))
    row_h = float(_geo(first).get("height", 18))
    at = min(elements.index(el) for el in row.values())
    out = [el for el in elements if not any(el is r for r in row.values())]
    new_cells = []
    for n, rev in enumerate(revisions):            # n = 0 oldest, bottom row
        for col, tpl in row.items():
            el = deepcopy(tpl)
            if n:
                el.set("id", f"{tpl.get('id')}_rev{n}")
            geo = _geo(el)
            geo.set("y", num(float(geo.get("y", 0)) - n * row_h))
            set_text(el, rev.get(col, ""))
            new_cells.append(el)
    return out[:at] + new_cells + out[at:], missing


def load_revisions(path: Path) -> list[dict]:
    """revHistory.csv rows (Rev, Date, Description, Drafter, Reviewer) in file
    order - add each new revision as the last line. Dates are shown as DD/MM/YY like the title block template;
    YYYY-MM-DD and YY-MM-DD are both accepted."""
    import csv
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items()}
                for r in csv.DictReader(f)]
    rows = [r for r in rows if any(r.values())]
    for r in rows:
        m = re.fullmatch(r"(\d{2}|\d{4})-(\d{1,2})-(\d{1,2})", r.get("Date", ""))
        if m:
            r["Date"] = f"{int(m[3]):02d}/{int(m[2]):02d}/{m[1][-2:]}"
    return rows


class Drawing:
    def __init__(self, name: str, page_w: int = 1654, page_h: int = 1169,
                 title_fields: dict | None = None, revisions: list | None = None):
        self.mxfile = ET.Element("mxfile", {"host": "Electron"})
        # Title block values (projectDetails.yml), keyed by background cell id.
        self.title_fields = dict(title_fields or {})
        # revHistory.csv rows, oldest first.
        self.revisions = list(revisions or [])
        self.warned_background = False
        self.add_page(name, page_w, page_h)

    def add_page(self, name: str, page_w: int = 1654, page_h: int = 1169,
                 drawing_name: str | None = None):
        """Start a new page (tab) in the same file. Everything drawn after
        this goes onto the new page. drawing_name is what the title block's
        Drawing Name box shows; default is the page name."""
        self.page_name = drawing_name or name
        diagram = ET.SubElement(self.mxfile, "diagram", {"name": name, "id": safe_id("ft_draw", name)})
        self.model = model = ET.SubElement(diagram, "mxGraphModel", {
            "dx": "1200", "dy": "900", "grid": "1", "gridSize": "8", "guides": "1",
            "tooltips": "1", "connect": "1", "arrows": "1", "fold": "1", "page": "1",
            "pageScale": "1", "pageWidth": str(page_w), "pageHeight": str(page_h),
            "math": "0", "shadow": "0",
        })
        self.root = ET.SubElement(model, "root")
        ET.SubElement(self.root, "mxCell", {"id": "0"})
        ET.SubElement(self.root, "mxCell", {"id": "1", "parent": "0", "value": "Wiring"})
        self.ids: set[str] = set()

    def box(self, id_, value, x, y, w, h, style, **attrs) -> Box:
        if id_ in self.ids:
            raise ConfigError(f"internal: duplicate cell id {id_}")
        self.ids.add(id_)
        cell = ET.SubElement(self.root, "mxCell", {
            "id": id_, "parent": "1", "vertex": "1", "value": str(value), "style": style,
            **{k: str(v) for k, v in attrs.items()},
        })
        ET.SubElement(cell, "mxGeometry", {
            "x": num(x), "y": num(y), "width": num(w), "height": num(h), "as": "geometry",
        })
        return Box(id_, x, y, w, h)

    def wire(self, a: Box, b: Box, route_key: str, bend_x: float | None = None,
             entry_y: float = 0.5, exit_y: float = 0.5, extra_style: str = ""):
        """Wire a -> b. If bend_x is given and the two ends are on different
        rows, the vertical part of the wire is placed at x = bend_x.
        entry_y is where on b's left edge the wire lands (0 top, 1 bottom)."""
        id_ = hash_id("edge", route_key, a.id, b.id)
        self.ids.add(id_)
        style = (EDGE_STYLE.replace("entryY=0.5", f"entryY={entry_y:.4g}")
                 .replace("exitY=0.5", f"exitY={exit_y:.4g}")) + extra_style
        cell = ET.SubElement(self.root, "mxCell", {
            "id": id_, "parent": "1", "edge": "1", "source": a.id, "target": b.id,
            "value": "", "style": style,
        })
        geo = ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
        y1, y2 = a.y + a.h * exit_y, b.y + b.h * entry_y
        if bend_x is not None and y1 != y2:
            pts = ET.SubElement(geo, "Array", {"as": "points"})
            ET.SubElement(pts, "mxPoint", {"x": num(bend_x), "y": num(y1)})
            ET.SubElement(pts, "mxPoint", {"x": num(bend_x), "y": num(y2)})

    def add_background(self, path: Path):
        """Copy every cell from a background .drawio (title block, border...)
        onto a 'Background' layer that sits underneath the wiring."""
        src = ET.parse(path).getroot()
        diagram = src.find("diagram")
        model = diagram.find("mxGraphModel") if diagram is not None else None
        if model is None or model.find("root") is None:
            raise ConfigError(f"{path}: not an uncompressed draw.io file "
                              "(in draw.io: File > Properties > untick Compressed, then save)")
        src_root = model.find("root")
        for k in ("pageWidth", "pageHeight"):
            if model.get(k):
                self.model.set(k, model.get(k))

        # Background cells go before layer "1" (the wiring) so they draw underneath.
        # In draw.io the first layer in the file is the bottom one.
        bg_layer = "background_layer"
        self.ids.add(bg_layer)
        incoming = [deepcopy(el) for el in src_root if el.get("id") not in ("0", "1")]
        fields = {"Drawing_Name": self.page_name, **self.title_fields}
        if self.revisions:
            latest = self.revisions[-1]
            fields.setdefault("Rev_Number", latest["Rev"])
            fields.setdefault("Rev_Details", latest["Description"])
        missing = [f"{k}{TEXT_SUFFIX}" for k in fill_title_block(incoming, fields)]
        incoming, no_rev = fill_rev_history(incoming, self.revisions)
        if self.revisions:
            missing += no_rev
        if missing and not self.warned_background:     # once, not per page
            print(f"NOTE: {path.name} has no cell with id {', '.join(missing)} - "
                  "those values are not shown", file=sys.stderr)
        self.warned_background = True
        renamed = {}
        for el in incoming:
            old = el.get("id")
            new = old if old not in self.ids else "bg_" + old
            renamed[old] = new
            self.ids.add(new)

        insert_at = 1  # straight after the root cell "0"
        layer = ET.Element("mxCell", {"id": bg_layer, "value": "Background", "parent": "0"})
        self.root.insert(insert_at, layer)
        insert_at += 1
        for el in incoming:
            el.set("id", renamed[el.get("id")])
            cell = el if el.tag == "mxCell" else el.find("mxCell")
            if cell is not None:
                parent = cell.get("parent")
                if parent == "1":
                    cell.set("parent", bg_layer)
                elif parent in renamed:
                    cell.set("parent", renamed[parent])
                for attr in ("source", "target"):
                    if cell.get(attr) in renamed:
                        cell.set(attr, renamed[cell.get(attr)])
            # Extra layers in the background file (e.g. "Title Block") keep
            # their own layer and also end up underneath the wiring.
            self.root.insert(insert_at, el)
            insert_at += 1

    def text(self, value, x, y, route_key: str):
        self.box(hash_id("lbl", route_key, value, x, y), value, x, y, LABEL_W, LABEL_H,
                 "text;html=1;strokeColor=none;fillColor=none;align=center;"
                 "verticalAlign=middle;fontSize=10;" + FONT)

    def save(self, path: Path):
        ET.indent(self.mxfile, space="  ")
        ET.ElementTree(self.mxfile).write(path, encoding="utf-8", xml_declaration=True)


# --------------------------------------------------------------------------
# Layout settings
# --------------------------------------------------------------------------
@dataclass
class Layout:
    origin_x: float
    origin_y: float
    row_h: float
    pitch: float
    gap: float
    term_x0: float
    term_step: float
    field_x: float
    field_label_w: float
    field_jog: float
    block_order: dict = field(default_factory=dict)
    block_info: dict = field(default_factory=dict)
    strip_area: tuple = (16, 1386)

    @classmethod
    def from_yaml(cls, cfg: dict) -> "Layout":
        o = cfg.get("controller_origin") or {}
        info_cfg = dict(cfg.get("terminal_block_info") or {})
        # Column order can be written in any of these places (later wins):
        #   layout.terminal_blocks                       (older files)
        #   layout.terminal_blocks_order
        #   layout.terminal_block_info.terminal_blocks_order
        #   layout.terminal_block_info.<block>.order
        order_sources = [cfg.get("terminal_blocks"), cfg.get("terminal_blocks_order"),
                         info_cfg.pop("terminal_blocks_order", None),
                         {k: v["order"] for k, v in info_cfg.items()
                          if isinstance(v, dict) and "order" in v}]
        blocks = {}
        for src in order_sources:
            for k, v in (src or {}).items():
                blocks[str(k).upper()] = int(v.get("order") if isinstance(v, dict) else v)
        info = {str(k).upper(): (v or {}) for k, v in info_cfg.items()}
        return cls(
            origin_x=float(o.get("x", 40)),
            origin_y=float(o.get("y", 80)),
            row_h=float(cfg.get("row_height", 32)),
            pitch=float(cfg.get("point_pitch_y", 96)),
            gap=float(cfg.get("point_vertical_gap", 8)),
            term_x0=float(cfg.get("terminal_origin_x", 435)),
            term_step=float(cfg.get("terminal_column_spacing", 210)),
            field_x=float(cfg.get("field_device_x", 1228)),
            field_label_w=float(cfg.get("field_device_width", 150)) - FIELD_W,
            # Wires that change row before a field device drop vertically
            # this far left of the device, i.e. tight to the right.
            field_jog=float(cfg.get("field_device_jog_offset", 12)),
            block_order=blocks,
            block_info=info,
            strip_area=(float((cfg.get("terminal_strip_area") or {}).get("left", 16)),
                        float((cfg.get("terminal_strip_area") or {}).get("right", 1386))),
        )

    def order(self, terminal: str) -> int:
        default = self.block_order.get("OTHER", max(self.block_order.values(), default=0) + 1)
        return self.block_order.get(terminal_block(terminal), default)

    def terminal_x(self, terminal: str) -> float:
        return self.term_x0 + (self.order(terminal) - 1) * self.term_step

    def is_common(self, terminal: str) -> bool:
        """True if the terminal (X8-002A) or its whole number (X8-002) is
        listed in that block's commons."""
        m = TERMINAL_RE.match(str(terminal).upper())
        if not m:
            return False
        commons = {str(c).upper() for c in (self.block_info.get(m["block"], {}).get("commons") or [])}
        return f"{m['block']}-{m['num']}{m['level']}" in commons or f"{m['block']}-{m['num']}" in commons

    def is_earth(self, terminal: str) -> bool:
        """True if the terminal's block has earth: true (e.g. X0)."""
        return bool(self.block_info.get(terminal_block(terminal), {}).get("earth"))

    def earth_stroke(self, terminal: str) -> str:
        colour = str(self.block_info.get(terminal_block(terminal), {}).get("color", "")).lower()
        return COLOURS.get(colour, (colour,))[0] if colour else "#60A917"


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def route_terminals(route: dict) -> list[str]:
    """All panel terminals on a route, controller side -> field side."""
    src, dst = route["from"], route["to"]
    out = [src["terminal"]] if "terminal" in src else []
    out += list(route["via"])
    out += [dst["terminal"]] if "terminal" in dst else []
    return out


def normalise_direction(points: dict, layout: Layout) -> None:
    """A wire between two panel terminals has no fixed direction, so if one
    is written field-side first (e.g. from X8-002B to X4-001) flip it so it
    runs controller -> field like everything else. via and labels flip too."""
    for tag, p in points.items():
        for rname, r in (p.get("wiring") or {}).items():
            src, dst = r.get("from") or {}, r.get("to") or {}
            if set(src) != {"terminal"} or set(dst) != {"terminal"}:
                continue
            if not isinstance(r.get("via"), list) or not isinstance(r.get("labels"), list):
                continue
            terms = route_terminals(r)
            orders = [layout.order(t) for t in terms]
            if orders != sorted(orders) and orders[::-1] == sorted(orders):
                r["from"], r["to"] = {"terminal": dst["terminal"]}, {"terminal": src["terminal"]}
                r["via"] = list(reversed(r["via"]))
                r["labels"] = list(reversed(r["labels"]))
                print(f"NOTE: {tag}.wiring.{rname} drawn as {dst['terminal']} -> {src['terminal']} "
                      "(written the other way round in points.yml)", file=sys.stderr)


# --------------------------------------------------------------------------
# Cables
# --------------------------------------------------------------------------
# cableTypes/*.yml holds the cable specs (cable_types: <key>: {...}); the
# project's cables.yml gives each physical cable a tag and a type; a wiring
# route names one of those cables and the core it uses on its field wire.
def load_cable_types(dirs: list[Path]) -> dict:
    types: dict = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for path in sorted(d.rglob("*.yml")) + sorted(d.rglob("*.yaml")):
            section = load_yaml(path).get("cable_types") or {}
            if not isinstance(section, dict):
                raise ConfigError(f"{path}: cable_types must be a mapping of type key -> details")
            for key, spec in section.items():
                if str(key) in types:
                    raise ConfigError(f"{path}: cable type {key!r} is already defined in another file")
                if not isinstance(spec, dict):
                    raise ConfigError(f"{path}: cable type {key!r} must be a mapping")
                types[str(key)] = {**spec, "_cores": cable_cores(spec, f"{path}: cable type {key!r}")}
    return types


def cable_cores(spec: dict, where: str) -> list[str]:
    """Core names a wire can use: pairs + pair_colours -> 1-white, 1-black,
    2-white...; or cores: [...] as written; plus "screen" if screen: true."""
    if "pairs" in spec and "cores" in spec:
        raise ConfigError(f"{where}: use pairs or cores, not both")
    if "pairs" in spec:
        n, colours = spec["pairs"], spec.get("pair_colours")
        if not isinstance(n, int) or n < 1:
            raise ConfigError(f"{where}: pairs must be a whole number >= 1")
        if not isinstance(colours, list) or len(colours) != 2:
            raise ConfigError(f"{where}: pair_colours must list the two colours of a pair")
        cores = [f"{i}-{c}" for i in range(1, n + 1) for c in colours]
    elif isinstance(spec.get("cores"), list) and spec["cores"]:
        cores = [str(c) for c in spec["cores"]]
    else:
        raise ConfigError(f"{where}: needs pairs + pair_colours, or a cores list")
    return cores + (["screen"] if spec.get("screen") else [])


def load_cables(path: Path, cable_types: dict) -> dict:
    cables = load_yaml(path).get("cables") or {}
    if not isinstance(cables, dict):
        raise ConfigError(f"{path}: cables must be a mapping of tag -> details")
    for tag, c in cables.items():
        ctype = (c or {}).get("type")
        if str(ctype) not in cable_types:
            raise ConfigError(f"{path}: cable {tag}: type {ctype!r} is not in the cable type library "
                              f"(have: {', '.join(cable_types) or 'none'})")
    return {str(k): v for k, v in cables.items()}


def cable_text(cable: dict) -> str:
    return f"{cable['tag']} / {cable['core']}"


def validate(points: dict, controllers: dict, ctrl_points: dict, layout: Layout,
             ignore_order: bool, cables: dict | None = None,
             cable_types: dict | None = None) -> None:
    cables, cable_types = cables or {}, cable_types or {}
    used = {}
    cores_used: dict[tuple, str] = {}
    for tag, p in points.items():
        ctrl = p.get("controller")
        cp = (p.get("controller_point") or {}).get("point")
        if ctrl not in controllers:
            raise ConfigError(f"{tag}: unknown controller {ctrl!r} (add it under controllers:)")
        if not cp:
            raise ConfigError(f"{tag}: controller_point.point is required")
        if cp not in ctrl_points[ctrl]:
            raise ConfigError(f"{tag}: point {cp!r} does not exist on {ctrl}'s controller template")
        if (ctrl, cp) in used:
            raise ConfigError(f"{tag}: {ctrl}.{cp} is already used by {used[(ctrl, cp)]}")
        used[(ctrl, cp)] = tag

        fifs = (p.get("field_device") or {}).get("interfaces") or {}
        if not fifs:
            raise ConfigError(f"{tag}: field_device.interfaces is required")
        orders = [v.get("order") for v in fifs.values()]
        if any(not isinstance(o, int) or o < 1 for o in orders) or len(set(orders)) != len(orders):
            raise ConfigError(f"{tag}: field_device.interfaces orders must be unique whole numbers >= 1")

        cifs = ctrl_points[ctrl][cp].get("interfaces") or {}
        for rname, r in (p.get("wiring") or {}).items():
            where = f"{tag}.wiring.{rname}"
            missing = [k for k in ("from", "via", "labels", "to") if k not in r]
            if missing:
                raise ConfigError(f"{where}: missing {', '.join(missing)}")
            if not isinstance(r["via"], list) or not isinstance(r["labels"], list):
                raise ConfigError(f"{where}: via and labels must be lists")
            if len(r["labels"]) != len(r["via"]) + 1:
                raise ConfigError(
                    f"{where}: needs {len(r['via']) + 1} labels (one per wire), has {len(r['labels'])}"
                )
            src, dst = r["from"], r["to"]
            if set(src) not in ({"point", "interface"}, {"terminal"}):
                raise ConfigError(f"{where}: from must be {{point, interface}} or {{terminal}}")
            if set(dst) not in ({"field_interface"}, {"terminal"}, {"cut_back"}):
                raise ConfigError(f"{where}: to must be {{field_interface}}, {{terminal}} or {{cut_back: true}}")
            if "cut_back" in dst and dst["cut_back"] is not True:
                raise ConfigError(f"{where}: cut_back must be true")
            if "point" in src:
                spoint = src["point"]
                if spoint not in ctrl_points[ctrl]:
                    raise ConfigError(f"{where}: from.point {spoint!r} not on {ctrl}'s controller template")
                sifs = ctrl_points[ctrl][spoint].get("interfaces") or {}
                if src["interface"] not in sifs:
                    raise ConfigError(
                        f"{where}: {spoint} has no interface {src['interface']!r} "
                        f"(template has: {', '.join(sifs)})"
                    )
                if spoint != cp:
                    # Only OK when the template says the two points share that terminal.
                    shared = sifs[src["interface"]].get("shared_connection")
                    mine = {d.get("shared_connection") for d in cifs.values()}
                    if not shared or shared not in mine:
                        raise ConfigError(
                            f"{where}: from.point is {spoint} but controller_point is {cp}, "
                            f"and they don't share {src['interface']!r} in the controller template"
                        )
            if "field_interface" in dst and dst["field_interface"] not in fifs:
                raise ConfigError(f"{where}: field_interface {dst['field_interface']!r} not declared")
            if r.get("cable") is not None:
                c = r["cable"]
                if not isinstance(c, dict) or set(c) != {"tag", "core"}:
                    raise ConfigError(f"{where}: cable must be {{tag, core}}, e.g. {{tag: CF-8-1, core: 1-white}}")
                if "terminal" in dst:
                    raise ConfigError(f"{where}: cable is for the wire out to the field device, "
                                      "but this route ends on a panel terminal")
                tag_, core = str(c["tag"]), str(c["core"])
                if tag_ not in cables:
                    raise ConfigError(f"{where}: cable {tag_!r} is not in cables.yml")
                ctype = str(cables[tag_]["type"])
                if core not in cable_types[ctype]["_cores"]:
                    raise ConfigError(f"{where}: cable {tag_} ({ctype}) has no core {core!r} "
                                      f"(has: {', '.join(cable_types[ctype]['_cores'])})")
                if (tag_, core) in cores_used:
                    raise ConfigError(f"{where}: core {core} of {tag_} is already used by {cores_used[(tag_, core)]}")
                cores_used[(tag_, core)] = where
                if core == "screen":
                    if "cut_back" not in dst:
                        raise ConfigError(f"{where}: a screen is earthed at the panel only, "
                                          "so the route must end with to: {cut_back: true}")
                    if "terminal" not in src or not layout.is_earth(src["terminal"]):
                        raise ConfigError(f"{where}: a screen must start on an earth terminal "
                                          "(a block with earth: true in terminal_block_info)")
            if "cut_back" in dst and (r.get("cable") or {}).get("core") != "screen":
                raise ConfigError(f"{where}: to: {{cut_back: true}} is for cable screens; "
                                  "add cable: {tag: ..., core: screen}")

            terms = route_terminals(r)
            orders = [layout.order(t) for t in terms]
            if orders != sorted(orders):
                suggested = sorted(terms, key=layout.order)
                msg = (f"{where}: terminals are not in terminal_blocks order\n"
                       f"  current:   {terms}\n  suggested: {suggested}")
                if not ignore_order:
                    raise ConfigError(msg + "\n  (re-run with --ignore-terminal-order to draw anyway)")
                print("WARNING: " + msg, file=sys.stderr)

    for tag_, c in cables.items():
        if not any(t == tag_ for t, _ in cores_used):
            print(f"WARNING: cable {tag_} is in cables.yml but no wire uses it", file=sys.stderr)
        elif "screen" in cable_types[str(c["type"])]["_cores"] and (tag_, "screen") not in cores_used:
            print(f"WARNING: cable {tag_} has a screen but no wire earths it "
                  "(add a route with cable: {tag: ..., core: screen})", file=sys.stderr)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
CABLE_FONT_PX = 6   # cable tag / core text under the wire label
CABLE_DY = 11       # offset from the wire label's top
# A cut-back screen always ends at the same place, whatever column its earth
# terminal is in: the tick sits CUT_BACK_FROM_FIELD left of the field device,
# with its labels centred CUT_BACK_LABEL_DX left of the tick (in line with
# the field-wire labels from the IO column).
CUT_BACK_FROM_FIELD = 106
CUT_BACK_LABEL_DX = 50.5
CUT_BACK_GAP = 8    # fallback: tick this far past the labels when the earth
                    # terminal is too far right for the fixed position
CUT_BACK_W, CUT_BACK_H = 2, 12   # the end tick


def render(points: dict, controllers: dict, ctrl_points: dict, layout: Layout,
           background: Path | None = None, title_fields: dict | None = None,
           revisions: list | None = None) -> Drawing:
    first = next(iter(controllers.values()))
    dwg = Drawing(first.get("drawing_name", "Field Connections"),
                  title_fields=title_fields, revisions=revisions)
    if background:
        dwg.add_background(background)
    terminals: dict[str, Box] = {}      # shared across the whole drawing
    shared: dict[tuple, Box] = {}       # (controller, shared_connection) -> box drawn first
    seen_routes: dict[tuple, str] = {}  # used only to warn about repeated routes
    L = layout
    x_type = L.origin_x + NAME_W + 13   # matches existing drawings (260 at origin 40)
    x_conn = x_type + TYPE_W

    y = L.origin_y
    for tag, p in points.items():
        ctrl = p["controller"]
        cp = p["controller_point"]["point"]
        tpl = ctrl_points[ctrl][cp]
        cifs = list((tpl.get("interfaces") or {}).items())
        fdev = p.get("field_device") or {}
        fifs = sorted(fdev["interfaces"].items(), key=lambda kv: kv[1]["order"])
        wiring = list((p.get("wiring") or {}).items())

        # ---- controller side -------------------------------------------
        dwg.box(safe_id(ctrl, cp, "name"),
                f"<b>{p.get('equipment', '')} - {tag}</b><br>{p.get('description', '')}",
                L.origin_x, y, NAME_W, NAME_H, BOX + "align=left;strokeColor=none;")
        ctrl_conn: dict[str, Box] = {}
        for i, (iname, idef) in enumerate(cifs):
            # Shared terminal (e.g. "C 1/2" used by UI1 and UI2): draw it once,
            # the first time it appears, and reuse it for later points.
            skey = (ctrl, idef["shared_connection"]) if idef.get("shared_connection") else None
            if skey in shared:
                ctrl_conn[iname] = shared[skey]
                continue
            ry = y + i * L.row_h
            title = tpl.get("display_name", cp) if i == 0 else idef.get("display_name", short_name(iname))
            dwg.box(safe_id(ctrl, cp, iname, "type"), title, x_type, ry, TYPE_W, L.row_h,
                    BOX + "fontStyle=1;")
            ctrl_conn[iname] = dwg.box(
                safe_id(ctrl, cp, iname), idef.get("terminal", ""), x_conn, ry, CONN_W, L.row_h,
                BOX + "points=[[1,0.5,1,0,0]];", object_type="point", point_type=iname.upper())
            if skey:
                shared[skey] = ctrl_conn[iname]

        # ---- field device side -----------------------------------------
        dwg.box(safe_id("dev", tag, "label"), fdev.get("label", tag),
                L.field_x + FIELD_W, y, L.field_label_w, len(fifs) * L.row_h,
                BOX + "strokeColor=none;")
        field_conn: dict[str, Box] = {}
        field_ids = set()
        for i, (iname, idef) in enumerate(fifs):
            field_conn[iname] = dwg.box(
                safe_id("dev", tag, iname), idef.get("label", iname.upper()),
                L.field_x, y + i * L.row_h, FIELD_W, L.row_h,
                BOX + "points=[[0,0.5,1,0,0]];", object_type="interface", interface_type=iname)
            field_ids.add(field_conn[iname].id)

        # ---- connections: pick a row for each, then draw ------------------
        # Rows are chosen so wires never run through another connection's
        # terminals, whatever order the wiring is written in points.yml:
        #   1. routes starting at the controller sit on that controller row,
        #   2. routes starting at a panel terminal take the row of the field
        #      terminal they land on, if it's free,
        #   3. otherwise the next row down where they don't overlap anything.
        def row_of(box: Box) -> int | None:
            k = round((box.y - y) / L.row_h)
            return k if 0 <= k and box.y < y + 1000 * L.row_h else None

        def start_box(src: dict) -> Box | None:
            if "point" not in src:
                return terminals.get(src["terminal"])
            sc = ctrl_points[ctrl][src["point"]]["interfaces"][src["interface"]].get("shared_connection")
            return shared[(ctrl, sc)] if sc and (ctrl, sc) in shared else ctrl_conn[src["interface"]]

        plans = []
        for idx, (rname, r) in enumerate(wiring):
            src, dst = r["from"], r["to"]
            sbox = start_box(src)
            x0 = sbox.x if sbox else L.terminal_x(src["terminal"])
            x1 = (L.field_x if "terminal" not in dst else L.terminal_x(dst["terminal"]) + TERM_W)
            prefer = None
            if "point" in src and sbox is not None:
                prefer = row_of(sbox)
            elif "field_interface" in dst:
                prefer = row_of(field_conn[dst["field_interface"]])
            # Placed in this order, each group in yml order: controller routes,
            # then other routes out to the field (screens included), then
            # panel-to-panel links, which fit in wherever is left.
            plans.append({"name": rname, "r": r, "span": (x0, x1), "prefer": prefer,
                          "rank": (0 if "point" in src else 1 if "terminal" not in dst else 2, idx)})

        taken: dict[int, list[tuple]] = {}     # row -> spans already used on it
        def free(row: int, span: tuple) -> bool:
            return all(span[1] <= a or span[0] >= b for a, b in taken.get(row, []))

        for plan in sorted(plans, key=lambda pl: pl["rank"]):
            cable = plan["r"].get("cable") or {}
            if cable.get("core") == "screen":
                # A screen goes on the row just under its cable's other cores.
                rows = [pl["row"] for pl in plans if "row" in pl
                        and (pl["r"].get("cable") or {}).get("tag") == cable.get("tag")]
                plan["prefer"] = max(rows) + 1 if rows else None
            candidates = ([plan["prefer"]] if plan["prefer"] is not None else []) + list(range(len(wiring) * 2 + 2))
            row = next(c for c in candidates if free(c, plan["span"]))
            taken.setdefault(row, []).append(plan["span"])
            plan["row"] = row

        for plan in plans:                     # draw in yml order (stable ids)
            rname, r = plan["name"], plan["r"]
            src, dst = r["from"], r["to"]
            if "point" in src:
                sdef = ctrl_points[ctrl][src["point"]]["interfaces"][src["interface"]]
                sc = sdef.get("shared_connection")
                start = ("shared", ctrl, sc) if sc else ("point", ctrl, src["point"], src["interface"])
            else:
                start = ("terminal", src["terminal"])
            if "terminal" in dst:
                end = ("terminal", dst["terminal"])
            elif "cut_back" in dst:
                end = ("cut_back", tag, rname)
            else:
                end = ("field", tag, dst["field_interface"])
            # Everything in points.yml is drawn. A route identical to an earlier
            # one is still drawn, but flagged in case it was entered by mistake.
            route_id = (start, tuple(r["via"]), tuple(map(str, r["labels"])), end)
            if route_id in seen_routes:
                print(f"WARNING: {tag}.wiring.{rname} is identical to {seen_routes[route_id]} "
                      "- drawn anyway; remove it from points.yml if it isn't needed", file=sys.stderr)
            else:
                seen_routes[route_id] = f"{tag}.wiring.{rname}"
            ry = y + plan["row"] * L.row_h
            key = f"{tag}.{rname}"

            def term(t: str) -> Box:
                if t not in terminals:
                    terminals[t] = dwg.box(safe_id("term", t), t, L.terminal_x(t),
                                           ry + (L.row_h - TERM_H) / 2, TERM_W, TERM_H,
                                           BOX + "fontSize=10;"
                                           + (COMMON_STROKE if L.is_common(t) else "")
                                           + (f"strokeColor={L.earth_stroke(t)};strokeWidth=2;"
                                              if L.is_earth(t) else ""))
                return terminals[t]

            chain = [start_box(src) or term(src["terminal"])]
            chain += [term(t) for t in r["via"]]
            if "field_interface" in dst:
                chain.append(field_conn[dst["field_interface"]])
            elif "cut_back" in dst:
                # Screen cut back and insulated at the device: a short tick
                # at a fixed distance left of the field device, on this
                # route's row (see CUT_BACK_FROM_FIELD).
                last = chain[-1]
                cut_x = L.field_x - CUT_BACK_FROM_FIELD
                cut_label_mid = cut_x - CUT_BACK_LABEL_DX
                if cut_label_mid - LABEL_W / 2 < last.right + CUT_BACK_GAP:
                    # Earth terminal too far right: labels just after it instead.
                    cut_label_mid = last.right + CUT_BACK_GAP + LABEL_W / 2
                    cut_x = cut_label_mid + LABEL_W / 2 + CUT_BACK_GAP
                chain.append(dwg.box(safe_id("cut", tag, rname), "",
                                     cut_x, ry + (L.row_h - CUT_BACK_H) / 2,
                                     CUT_BACK_W, CUT_BACK_H,
                                     "rounded=0;html=1;fillColor=#000000;strokeColor=#000000;"))
            else:
                chain.append(term(dst["terminal"]))
            is_screen = (r.get("cable") or {}).get("core") == "screen"
            wire_style = (f"dashed=1;strokeColor={L.earth_stroke(src['terminal'])};"
                          if is_screen else "")
            field_end = chain[-1].id if "terminal" not in dst else None
            cut_id = chain[-1].id if "cut_back" in dst else None

            for (a, b), text in zip(zip(chain, chain[1:]), r["labels"]):
                if b.id in field_ids:
                    bend = L.field_x - L.field_jog          # tight to the field device
                elif "point" in src and a is chain[0]:
                    bend = a.right + L.field_jog            # tight to the controller
                else:
                    bend = None
                dwg.wire(a, b, key, bend_x=bend, extra_style=wire_style)
                mid = cut_label_mid if b.id == cut_id else (a.right + b.x) / 2
                ya, yb = a.y + a.h / 2, b.y + b.h / 2
                # Label sits on the wire: its own row, or the row of an
                # existing straight wire it shares (e.g. a repeated route).
                line_y = ya if abs(ya - yb) < 0.5 else ry + L.row_h / 2
                if text not in (None, ""):
                    dwg.text(text, mid - LABEL_W / 2, line_y - LABEL_H, key)
                if r.get("cable") and b.id == field_end:
                    # Cable tag and core just under the field wire's label.
                    dwg.text(f'<font style="font-size: {CABLE_FONT_PX}px;">{cable_text(r["cable"])}</font>',
                             mid - LABEL_W / 2, line_y - LABEL_H + CABLE_DY, key)

        # Block height = longest of controller rows, field rows, drawn connections.
        used_rows = max((pl["row"] + 1 for pl in plans), default=0)
        rows = max(len(cifs), len(fifs), used_rows, 1)
        y += max(L.pitch, rows * L.row_h + L.gap)

    return dwg


# --------------------------------------------------------------------------
# Terminal strip pages
# --------------------------------------------------------------------------
# Pages added after the points page, one per terminal block (X3, X4, X8...).
# Each level of a block is a column: single = A only, double = A and B,
# triple = A, B and C. Blocks that share a terminal_blocks_order and have
# 3 columns or fewer between them share a page (X3 + X4, X5 + X6).
# Earth blocks (earth: true, e.g. X0) always get a page of their own.
#
# Every terminal is listed in number order with each panel wire on it. Wires
# out to field devices are left off, unless the terminal has no panel wire
# (then the field wire is shown, e.g. X3-008 -> PT01-SRP-OAT-(24v)). A terminal with two or more wires is
# drawn taller so each wire lands on it:
#   normal:         [X4-001] ---- 48-001 ---- [X8-002B]   (strip terminal left)
#   reversed: true: [X8-001A] ---- 38-001 ---- [X3-001]   (strip terminal right)
#
# The columns are centred on the drawing area (layout.terminal_strip_area,
# default x 16 to 1386, the space left of the title block).
STRIP_GROUP_W = 368     # distance between columns
STRIP_TERM_DX = 272     # left box -> right box
STRIP_ROW = 22          # row pitch
STRIP_TITLE_Y, STRIP_HEAD_Y, STRIP_Y0 = 22, 60, 94
STRIP_SRC_TEXT_W = 110  # width of a non-terminal "other end" text
STRIP_COL_W = STRIP_TERM_DX + TERM_W


def wire_segments(points: dict, ctrl_points: dict) -> list[tuple]:
    """Every individual wire in points.yml as (end_a, end_b, label).
    An end is ("terminal", "X8-001A"), ("text", "UI1-C-(2)") for a controller
    terminal, or ("field", "TT12-OAT-(+)") for a field device."""
    out = []
    for tag, p in points.items():
        ctrl = p["controller"]
        fifs = (p.get("field_device") or {}).get("interfaces") or {}
        for r in (p.get("wiring") or {}).values():
            src, dst = r["from"], r["to"]
            if "point" in src:
                idef = ctrl_points[ctrl][src["point"]]["interfaces"][src["interface"]]
                ends = [("text", f"{src['point']}-{src['interface'][:1].upper()}-({idef.get('terminal', '?')})")]
            else:
                ends = [("terminal", src["terminal"])]
            ends += [("terminal", t) for t in r["via"]]
            if "terminal" in dst:
                ends.append(("terminal", dst["terminal"]))
            elif "cut_back" in dst:
                ends.append(("field", f"{tag}-(screen)"))
            else:
                flabel = fifs[dst["field_interface"]].get("label", dst["field_interface"].upper())
                ends.append(("field", f"{tag}-({flabel})"))
            out += [(a, b, "" if lbl is None else str(lbl))
                    for (a, b), lbl in zip(zip(ends, ends[1:]), r["labels"])]
    return out


def terminal_style(layout: Layout, terminal: str) -> str:
    m = TERMINAL_RE.match(str(terminal).upper())
    colour = str((layout.block_info.get(m["block"], {}) if m else {}).get("color", "")).lower()
    fill, font = COLOURS.get(colour, (colour, "#000000") if colour.startswith("#") else ("#FFFFFF", "#000000"))
    return (BOX + f"fontSize=10;fillColor={fill};fontColor={font};"
            + (COMMON_STROKE if layout.is_common(terminal) else ""))


def plan_strips(segments: list[tuple], layout: Layout) -> list[dict]:
    """Work out which strips to draw, their levels, terminals and wires."""
    used: dict[str, dict] = {}     # block -> {terminal name: [(other end, label)]}
    field: dict[str, dict] = {}    # same, wires out to field devices only
    for a, b, lbl in segments:
        for me, other in ((a, b), (b, a)):
            if me[0] != "terminal":
                continue
            t = str(me[1]).upper()
            if not TERMINAL_RE.match(t):
                if other[0] != "field":
                    print(f"NOTE: {me[1]} isn't in BLOCK-NUMBER form, left off the terminal strip pages",
                          file=sys.stderr)
                continue
            target = field if other[0] == "field" else used
            target.setdefault(TERMINAL_RE.match(t)["block"], {}).setdefault(me[1], []).append((other, lbl))
    # Field wiring is only shown on a terminal with no panel wiring, e.g. an
    # X3 supply wired straight to the device - otherwise it would be missing.
    for block, terms in field.items():
        for t, conns in terms.items():
            if not used.get(block, {}).get(t):
                used.setdefault(block, {})[t] = conns

    strips = []
    for block in sorted(used, key=lambda b: (layout.block_order.get(b, 99), b)):
        info = layout.block_info.get(block, {})
        written = {t: TERMINAL_RE.match(t.upper()) for t in used[block]}
        kind = str(info.get("type") or "").lower()
        if not kind:   # not described in terminal_block_info: go by the letters used
            top = max((m["level"] for m in written.values()), default="") or "A"
            kind = {"A": "single", "B": "double"}.get(top, "triple")
        if kind not in LEVELS:
            raise ConfigError(f"terminal_block_info.{block}.type must be one of "
                              f"{', '.join(LEVELS)} (got {kind!r})")
        levels = LEVELS[kind]
        for t, m in written.items():
            if len(levels) == 1 and m["level"] not in ("", "A"):
                raise ConfigError(f"{t}: {block} is a {kind} terminal block, so it has no level {m['level']}")
            if len(levels) > 1 and (not m["level"] or m["level"] not in levels):
                raise ConfigError(f"{t}: {block} is a {kind} terminal block - write the level "
                                  f"letter on the end ({', '.join(f'{t}{l}' for l in levels)})")
        commons = [TERMINAL_RE.match(str(c).upper()) for c in (info.get("commons") or [])]
        numbers = [int(m["num"]) for m in written.values()] + [int(m["num"]) for m in commons if m]
        pad = len(next(iter(written.values()))["num"])
        by_written = {(int(m["num"]), m["level"] or "A"): t for t, m in written.items()}

        rows = []                  # one entry per terminal number
        for n in range(1, max(numbers) + 1):
            cells = []
            for lvl in levels:
                name = by_written.get((n, lvl)) or (
                    f"{block}-{n:0{pad}d}" + (lvl if len(levels) > 1 else ""))
                cells.append((name, used[block].get(name, [])))
            rows.append(cells)
        name = f"{block} - {info['label']}" if info.get("label") else block
        strips.append({"block": block, "name": name, "levels": levels, "rows": rows,
                       "reversed": bool(info.get("reversed", False)),
                       "order": layout.block_order.get(block, 99),
                       "earth": bool(info.get("earth"))})
    return strips


def group_strip_pages(strips: list[dict]) -> list[list[dict]]:
    """Blocks with the same order share a page while they fit in 3 columns.
    Earth blocks (earth: true) always get a page of their own."""
    pages: list[list[dict]] = []
    for st in strips:
        last = pages[-1] if pages else None
        if (last and last[0]["order"] == st["order"]
                and not st["earth"] and not last[0]["earth"]
                and sum(len(x["levels"]) for x in last) + len(st["levels"]) <= 3):
            last.append(st)
        else:
            pages.append([st])
    return pages


def render_strips(dwg: Drawing, segments: list[tuple], layout: Layout,
                  background: Path | None, page_h: float = 1169) -> int:
    area_l, area_r = layout.strip_area
    pages = 0
    for group in group_strip_pages(plan_strips(segments, layout)):
        # Columns on this page: (strip, level index)
        cols = [(st, i) for st in group for i in range(len(st["levels"]))]
        total_w = (len(cols) - 1) * STRIP_GROUP_W + STRIP_COL_W
        x_left = (area_l + area_r) / 2 - total_w / 2
        base = "Terminal Strip " + " / ".join(st["name"] for st in group)
        # Title block Drawing Name: blocks only, e.g. "Terminal Strip X3 / X4"
        short = "Terminal Strip " + " / ".join(st["block"] for st in group)
        n_numbers = max(len(st["rows"]) for st in group)

        part, y, bottom = 0, None, page_h - 40
        for n in range(n_numbers):
            cells = [st["rows"][n][i] if n < len(st["rows"]) else None for st, i in cols]
            n_rows = max([1] + [len(c[1]) for c in cells if c])
            height = (n_rows - 1) * STRIP_ROW + TERM_H
            if y is None or y + height > bottom:      # start a (continuation) page
                part += 1
                suffix = f" ({part})" if part > 1 else ""
                title = base + suffix
                dwg.add_page(title, drawing_name=short + suffix)
                pages += 1
                if background:
                    dwg.add_background(background)
                    bottom = float(dwg.model.get("pageHeight", page_h)) - 40
                dwg.box(safe_id("strip_title", part), f"<b>{title}</b>", x_left,
                        STRIP_TITLE_Y, total_w, 32,
                        "text;html=1;strokeColor=none;fillColor=none;align=center;fontSize=14;")
                for c, (st, i) in enumerate(cols):
                    lvl = st["levels"][i]
                    if len(group) == 1:
                        head = f"{lvl} Terminals" if len(st["levels"]) > 1 else "Terminals"
                    else:
                        head = st["name"] + (f" - {lvl} Terminals" if len(st["levels"]) > 1 else "")
                    dwg.box(safe_id("strip_head", part, st["block"], lvl), head,
                            x_left + c * STRIP_GROUP_W, STRIP_HEAD_Y, STRIP_COL_W, 18,
                            "text;html=1;strokeColor=none;fillColor=none;align=center;")
                y = STRIP_Y0

            for c, cell in enumerate(cells):
                if cell is None:
                    continue
                st = cols[c][0]
                name, conns = cell
                gx = x_left + c * STRIP_GROUP_W
                # normal: strip terminal on the left; reversed: on the right
                tx, ox = (gx + STRIP_TERM_DX, gx) if st["reversed"] else (gx, gx + STRIP_TERM_DX)
                term = dwg.box(safe_id("strip", name), name, tx, y, TERM_W, height,
                               terminal_style(layout, name))
                for j, ((kind, other), lbl) in enumerate(conns):
                    cy = y + TERM_H / 2 + j * STRIP_ROW
                    sid = hash_id("strip_src", name, j, other)
                    if kind == "terminal":
                        oth = dwg.box(sid, other, ox, cy - TERM_H / 2, TERM_W, TERM_H,
                                      terminal_style(layout, other))
                    else:
                        text_x = ox + TERM_W - STRIP_SRC_TEXT_W if st["reversed"] else ox
                        oth = dwg.box(sid, other, text_x, cy - TERM_H / 2, STRIP_SRC_TEXT_W, TERM_H,
                                      BOX + "fontSize=10;strokeColor=none;fillColor=none;"
                                      + ("align=right;" if st["reversed"] else "align=left;"))
                    frac = (cy - y) / height
                    if st["reversed"]:
                        dwg.wire(oth, term, f"strip.{name}.{j}", entry_y=frac)
                    else:
                        dwg.wire(term, oth, f"strip.{name}.{j}", exit_y=frac)
                    if lbl:
                        mid = gx + (TERM_W + STRIP_TERM_DX) / 2
                        dwg.text(lbl, mid - LABEL_W / 2, cy - LABEL_H, f"strip.{name}.{j}")
            y += n_rows * STRIP_ROW
    return pages


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("points", type=Path, help="project points.yml")
    ap.add_argument("-o", "--output", type=Path, default=Path("output.drawio"))
    ap.add_argument("--controller-library", type=Path, action="append", default=[],
                    help="extra folder to search for controller template YAML (repeatable)")
    ap.add_argument("--ignore-terminal-order", action="store_true",
                    help="warn instead of failing when a route's terminals are out of order")
    ap.add_argument("--background", type=Path,
                    help="background .drawio (title block/border). Default: "
                         "layout.background in points.yml, else backgroundTemplates/Background.drawio")
    ap.add_argument("--no-background", action="store_true", help="draw without a background")
    ap.add_argument("--project-details", type=Path,
                    help="title block values. Default: projectDetails.yml next to points.yml")
    ap.add_argument("--rev-history", type=Path,
                    help="revision history CSV. Default: revHistory.csv next to points.yml")
    ap.add_argument("--cables", type=Path,
                    help="project cable list. Default: cables.yml next to points.yml")
    ap.add_argument("--cable-library", type=Path, action="append", default=[],
                    help="extra folder to search for cable type YAML (repeatable)")
    ap.add_argument("--no-terminal-strips", action="store_true",
                    help="only draw the points page, no terminal strip pages")
    a = ap.parse_args()

    project = load_yaml(a.points)
    if project.get("schema_version") != 4:
        raise ConfigError(f"schema_version must be 4, got {project.get('schema_version')!r}")
    points = project.get("points")
    if not isinstance(points, dict) or not points:
        raise ConfigError("points: must contain at least one point")
    controllers = project.get("controllers") or {}
    if not controllers:
        raise ConfigError("controllers: must list at least one controller")

    here = Path(__file__).resolve().parent
    search = a.controller_library + [a.points.parent, here / "controllerTemplates", here]
    ctrl_points = {}
    for name, cfg in controllers.items():
        path = find_controller_template(cfg.get("template", ""), search)
        ctrl_points[name] = load_controller_points(path)

    layout_cfg = project.get("layout") or {}
    layout = Layout.from_yaml(layout_cfg)
    normalise_direction(points, layout)
    cable_types = load_cable_types(a.cable_library + [a.points.parent / "cableTypes", here / "cableTypes"])
    cables = {}
    cables_file = a.cables or a.points.parent / "cables.yml"
    if cables_file.is_file():
        cables = load_cables(cables_file, cable_types)
    elif a.cables:
        raise ConfigError(f"cables file not found: {cables_file}")
    validate(points, controllers, ctrl_points, layout, a.ignore_terminal_order, cables, cable_types)

    background = None
    if not a.no_background:
        if a.background:
            background = a.background
        elif layout_cfg.get("background"):
            background = here / layout_cfg["background"]
        elif (here / "backgroundTemplates" / "Background.drawio").is_file():
            background = here / "backgroundTemplates" / "Background.drawio"
        if background and not background.is_file():
            raise ConfigError(f"background file not found: {background}")

    title_fields = {}
    details = a.project_details or a.points.parent / "projectDetails.yml"
    if details.is_file():
        raw = load_yaml(details)
        # Keys may sit under a top-level "Project:" section or at the top level.
        section = raw.get("Project") if isinstance(raw.get("Project"), dict) else raw
        title_fields = {str(k): v for k, v in section.items() if not isinstance(v, (dict, list))}
    elif a.project_details:
        raise ConfigError(f"project details file not found: {details}")

    revisions = []
    rev_file = a.rev_history or a.points.parent / "revHistory.csv"
    if rev_file.is_file():
        revisions = load_revisions(rev_file)
    elif a.rev_history:
        raise ConfigError(f"revision history file not found: {rev_file}")

    dwg = render(points, controllers, ctrl_points, layout, background, title_fields, revisions)
    strip_pages = 0
    if not a.no_terminal_strips:
        strip_pages = render_strips(dwg, wire_segments(points, ctrl_points), layout, background)
    dwg.save(a.output)

    n_routes = sum(len(p.get("wiring") or {}) for p in points.values())
    print(f"Drew {len(points)} point(s), {n_routes} connection(s), "
          f"{strip_pages} terminal strip page(s) -> {a.output}"
          + (f" (background: {background.name})" if background else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
