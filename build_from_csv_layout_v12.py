#!/usr/bin/env python3
"""FT-Draw CSV to Draw.io renderer.

Inputs:
  controllerTemplates/SNC25152.yml
  drawioTemplates/controllers/SNC25152.drawio
  deviceTemplates/**/<template>.yml
  drawioTemplates/devices/<template>.drawio
  project points.csv

Example:
  python3 build_from_csv_layout_v12.py project/testProject/points.csv --output output.drawio
"""
from __future__ import annotations
import argparse, copy, csv, sys, uuid
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml

ROOT = Path(__file__).resolve().parent
CONTROLLER_YAML = ROOT / "controllerTemplates/SNC25152.yml"
CONTROLLER_DRAWIO = ROOT / "drawioTemplates/controllers/SNC25152.drawio"
DEVICE_YAML_ROOT = ROOT / "deviceTemplates"
DEVICE_DRAWIO_ROOT = ROOT / "drawioTemplates/devices"

FIELD_X = 1001.0
MSSB_X = 756.0
TERMINAL_X = 520.0
PANEL_LABEL_X = 446.0
EXTERNAL_LABEL_X = 620.0
FIELD_END_LABEL_X = 926.0
MSSB_END_LABEL_X = 697.0
LABEL_W, LABEL_H, LABEL_GAP = 50.0, 16.0, 3.0
TERM_W, TERM_H = 60.0, 12.0
COMMON_OFFSET = 20.0
TRUNK_OFFSET = 25.0

EDGE = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;endArrow=none;strokeWidth=2;jumpStyle=arc;"
COMMON_EDGE = EDGE + "entryX=1;entryY=0.5;entryDx=0;entryDy=0;"


def args():
    p=argparse.ArgumentParser()
    p.add_argument("points_csv", type=Path)
    p.add_argument("--output", type=Path, default=Path("output.drawio"))
    return p.parse_args()


def load_yaml(p):
    with Path(p).open(encoding="utf-8") as f: return yaml.safe_load(f) or {}


def load_drawio(p):
    tree=ET.parse(p); root=tree.getroot().find(".//root")
    if root is None: raise ValueError(f"No mxGraph root in {p}")
    return tree,root


def named_file(root, stem, suffix):
    found=[p for p in Path(root).rglob(f"*{suffix}") if p.stem.casefold()==stem.casefold()]
    if len(found)!=1: raise ValueError(f"Expected one {stem}{suffix}, found {found}")
    return found[0]


def find_obj(root, oid):
    for o in root.iter("object"):
        if o.get("id")==oid: return o
    raise ValueError(f"Object not found: {oid}")


def maybe_obj(root, oid):
    try:return find_obj(root,oid)
    except ValueError:return None


def cell_of(e): return e.find("mxCell") if e.tag=="object" else e

def geo_of(e):
    g=cell_of(e).find("mxGeometry")
    if g is None: raise ValueError(f"No geometry: {e.get('id')}")
    return g


def bounds(e):
    g=geo_of(e); return tuple(float(g.get(k,0)) for k in ("x","y","width","height"))


def centre(e):
    x,y,w,h=bounds(e); return x+w/2,y+h/2


def all_cells(root): return {c.get("id"):c for c in root.iter("mxCell") if c.get("id")}


def absolute_xy(cell,cells):
    g=cell.find("mxGeometry"); x=float(g.get("x",0)) if g is not None else 0; y=float(g.get("y",0)) if g is not None else 0
    parent=cell.get("parent"); seen=set()
    while parent and parent not in {"0","1"} and parent not in seen:
        seen.add(parent); pc=cells.get(parent)
        if pc is None: break
        pg=pc.find("mxGeometry")
        if pg is not None: x+=float(pg.get("x",0)); y+=float(pg.get("y",0))
        parent=pc.get("parent")
    return x,y


def absolute_centre(e,root):
    c=cell_of(e); x,y=absolute_xy(c,all_cells(root)); g=c.find("mxGeometry")
    return x+float(g.get("width",0))/2, y+float(g.get("height",0))/2


def flatten(template_root,prefix):
    cells=all_cells(template_root); leaves=[]; mapping={}
    for child in list(template_root):
        if child.tag not in {"object","mxCell"}: continue
        c=cell_of(child); oid=child.get("id")
        if c is None or not oid or oid in {"0","1"} or c.get("vertex")!="1" or "group" in c.get("style",""): continue
        clone=copy.deepcopy(child); cc=cell_of(clone); g=cc.find("mxGeometry")
        if g is None: continue
        x,y=absolute_xy(c,cells); g.set("x",str(x)); g.set("y",str(y)); cc.set("parent","1")
        nid=f"{prefix}_{oid}"; clone.set("id",nid); mapping[oid]=nid; leaves.append(clone)
    return leaves,mapping


def move(e,dx,dy):
    g=geo_of(e); g.set("x",str(float(g.get("x",0))+dx)); g.set("y",str(float(g.get("y",0))+dy))


def set_by_id(root,oid,value):
    for o in root.iter("object"):
        if o.get("id")==oid:
            o.set("label",value); c=o.find("mxCell")
            if c is not None:c.set("value",value)
            return True
    for c in root.iter("mxCell"):
        if c.get("id")==oid:c.set("value",value); return True
    return False


def set_description(root,point,equipment,tag,description):
    title=" - ".join(x for x in (equipment,tag) if x)
    value=f"<b>{title}</b><br>{description}" if title and description else (f"<b>{title}</b>" if title else description)
    for oid in (f"{point}_Description",f"{point}Description",f"{point}_DESCRIPTION",f"{point}_DETAILS"):
        if set_by_id(root,oid,value): return


def add_text(root,oid,text,x,cy):
    if not text:return
    c=ET.Element("mxCell",{"id":oid,"parent":"1","vertex":"1","value":text,"style":"text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontFamily=Courier New;fontSize=10;"})
    ET.SubElement(c,"mxGeometry",{"x":str(x),"y":str(cy-LABEL_H-LABEL_GAP+5),"width":str(LABEL_W),"height":str(LABEL_H),"as":"geometry"}); root.append(c)


def add_terminal(root,oid,text,cy):
    c=ET.Element("mxCell",{"id":oid,"parent":"1","vertex":"1","value":text,"style":"rounded=0;whiteSpace=wrap;html=1;fontSize=10;"})
    ET.SubElement(c,"mxGeometry",{"x":str(TERMINAL_X),"y":str(cy-TERM_H/2),"width":str(TERM_W),"height":str(TERM_H),"as":"geometry"}); root.append(c)


def add_edge(root,oid,source,target,style,points=None):
    c=ET.Element("mxCell",{"id":oid,"parent":"1","edge":"1","source":source,"target":target,"value":"","style":style})
    g=ET.SubElement(c,"mxGeometry",{"relative":"1","as":"geometry"})
    if points:
        g.set("x","-0.039"); a=ET.SubElement(g,"Array",{"as":"points"})
        for x,y in points:ET.SubElement(a,"mxPoint",{"x":str(x),"y":str(y)})
    root.append(c)


def boundary(root,oid,x):
    c=ET.Element("mxCell",{"id":oid,"parent":"1","edge":"1","value":"","style":"endArrow=none;dashed=1;html=1;rounded=0;defaultLabelBackgroundColor=page;"})
    g=ET.SubElement(c,"mxGeometry",{"relative":"1","width":"50","height":"50","as":"geometry"})
    ET.SubElement(g,"mxPoint",{"x":str(x),"y":"925","as":"sourcePoint"}); ET.SubElement(g,"mxPoint",{"x":str(x),"y":"25","as":"targetPoint"}); root.append(c)


def route(root,prefix,role,source,target,target_obj,row,is_mssb):
    mode=(row.get(f"{role}_terminal_mode") or "").strip().lower(); terminal=(row.get(f"{role}_terminal_id") or "").strip()
    panel=(row.get(f"{role}_wire") or "").strip(); external=(row.get(f"{role}_core") or "").strip()
    source_obj=find_obj(root,source); _,sy=centre(source_obj); tx,ty=absolute_centre(target_obj,root)
    style=COMMON_EDGE if role=="common" else EDGE
    way=[(tx+TRUNK_OFFSET,sy),(tx+TRUNK_OFFSET,ty)] if role=="common" else None
    if mode not in {"terminal","mssb"} or not terminal:
        add_edge(root,f"edge_{prefix}_{role}",source,target,style,way); add_text(root,f"edge_{prefix}_{role}_label",panel or external,PANEL_LABEL_X,sy); return
    tid=f"terminal_{prefix}_{role}"; add_terminal(root,tid,terminal,sy)
    add_edge(root,f"edge_{prefix}_{role}_external",source,tid,EDGE)
    add_text(root,f"edge_{prefix}_{role}_external_label",external,EXTERNAL_LABEL_X,sy)
    add_text(root,f"edge_{prefix}_{role}_end_label",external,MSSB_END_LABEL_X if is_mssb else FIELD_END_LABEL_X,sy)
    add_edge(root,f"edge_{prefix}_{role}_panel",tid,target,style,way); add_text(root,f"edge_{prefix}_{role}_panel_label",panel,PANEL_LABEL_X,sy)


def main():
    a=args(); controller=load_yaml(CONTROLLER_YAML); tree,out=load_drawio(CONTROLLER_DRAWIO)
    with a.points_csv.open(newline="",encoding="utf-8-sig") as f: rows=list(csv.DictReader(f))
    mssb_lines=False; built=0
    for n,row in enumerate(rows,1):
        point=(row.get("controller_point") or "").strip(); template=(row.get("device_template") or "").strip(); tag=(row.get("point_tag") or f"POINT-{n}").strip()
        if not point or not template: print(f"SKIP row {n}: missing point/template"); continue
        pdata=controller["points"][point]; sig_target=pdata["interfaces"]["signal"]["drawio_id"]; com_config=pdata["interfaces"]["common"]["drawio_id"]
        sig_obj=find_obj(out,sig_target); com_obj=maybe_obj(out,f"z{com_config}") or find_obj(out,com_config); com_target=com_obj.get("id")
        dyaml=load_yaml(named_file(DEVICE_YAML_ROOT,template,".yml")); _,droot=load_drawio(named_file(DEVICE_DRAWIO_ROOT,template,".drawio"))
        interfaces=dyaml["device_template"]["drawing"]["interfaces"]; source_ids={k:v.get("drawio_id",v.get("id",k)) for k,v in interfaces.items()}
        prefix=f"d{n}_{uuid.uuid4().hex[:8]}"; leaves,mapid=flatten(droot,prefix); ss=mapid[source_ids["signal"]]; cs=mapid[source_ids["common"]]
        temp=ET.Element("root"); [temp.append(x) for x in leaves]; sl=find_obj(temp,ss); cl=find_obj(temp,cs)
        is_mssb=(row.get("destination_type") or "").strip().lower()=="mssb" or (row.get("signal_terminal_mode") or "").strip().lower()=="mssb"
        endpoint_x=MSSB_X if is_mssb else FIELD_X
        if is_mssb and not mssb_lines: boundary(out,"mssb_boundary_left",680); boundary(out,"mssb_boundary_right",918); mssb_lines=True
        _,target_y=absolute_centre(sig_obj,out); sx,sy=centre(sl); dx=endpoint_x+bounds(sl)[2]/2-sx; dy=target_y-sy
        for x in leaves:move(x,dx,dy)
        sl=find_obj(temp,ss); cl=find_obj(temp,cs); _,sigy=centre(sl); _,comy=absolute_centre(com_obj,out); desired=sigy+(COMMON_OFFSET if sigy<comy else -COMMON_OFFSET); g=geo_of(cl); g.set("y",str(desired-float(g.get("height",0))/2))
        # align body to upper terminal
        bodies=[(bounds(x)[2],geo_of(x)) for x in leaves if cell_of(x).get("vertex")=="1" and bounds(x)[2]>40]
        if bodies:max(bodies,key=lambda z:z[0])[1].set("y",str(min(bounds(sl)[1],bounds(cl)[1])))
        # endpoint/body labels
        for x in leaves:
            c=cell_of(x)
            if template in c.get("value",""):c.set("value",f'<span style="font-family: &quot;Courier New&quot;;">{tag}</span>')
        dst_sig=(row.get("signal_destination_terminal") or "").strip(); dst_com=(row.get("common_destination_terminal") or "").strip()
        if dst_sig:sl.set("label",dst_sig)
        if dst_com:cl.set("label",dst_com)
        for x in leaves:out.append(x)
        set_description(out,point,(row.get("equipment") or "").strip(),tag,(row.get("description") or "").strip())
        route(out,prefix,"signal",ss,sig_target,sig_obj,row,is_mssb); route(out,prefix,"common",cs,com_target,com_obj,row,is_mssb)
        built+=1; print(f"BUILT {tag}: {point} -> {'mssb' if is_mssb else 'field'}")
    if not built:raise ValueError("No points built")
    a.output.parent.mkdir(parents=True,exist_ok=True); ET.indent(tree,space="  "); tree.write(a.output,encoding="utf-8",xml_declaration=True); print(f"Created {a.output}: {built} points")

if __name__=="__main__":
    try:main()
    except Exception as e:print(f"ERROR: {e}",file=sys.stderr); raise SystemExit(1)
