#!/usr/bin/env python3
"""Field-first Draw.io renderer for explicit points.yml schema_version 4."""
import argparse, hashlib, re, sys
from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml

RENDERER_NAME = "field-first-v4"
RENDERER_REVISION = 1
ROW_H = 32.0
TERM_W, TERM_H = 60.0, 12.0
EDGE_STYLE = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;endArrow=none;strokeWidth=2;jumpStyle=arc;"

class ConfigError(Exception): pass

def args():
    p=argparse.ArgumentParser(description="Render schema_version 4 points.yml to Draw.io")
    p.add_argument("points")
    p.add_argument("--output", default="output.drawio")
    p.add_argument("--repo", default=".")
    p.add_argument("--ignore-terminal-order", action="store_true")
    p.add_argument("--controller-library", help="Controller YAML directory")
    return p.parse_args()

def sid(*parts):
    return re.sub(r"[^A-Za-z0-9_]+", "_", "_".join(map(str,parts))).strip("_")

def uid(prefix,*parts):
    h=hashlib.sha1("|".join(map(str,parts)).encode()).hexdigest()[:10]
    return f"{prefix}_{h}"

def load_yaml(path):
    with open(path, encoding="utf-8") as f: return yaml.safe_load(f) or {}

def controller_file(repo, configured, extra=None):
    names=[str(configured), f"{configured}.yml", f"{configured}.yaml"]
    roots=[Path(extra)] if extra else []
    roots += [Path(repo), Path(repo)/"controllers", Path(repo)/"templates"/"controllers", Path(__file__).parent]
    for root in roots:
        for n in names:
            p=root/n
            if p.is_file(): return p
        for ext in ("*.yml","*.yaml"):
            for p in root.rglob(ext):
                if p.stem.lower()==str(configured).lower(): return p
    raise ConfigError(f"controller template YAML not found for {configured}")

def vertex(root, id_, value, x,y,w,h, style, **attrs):
    c=ET.SubElement(root,"mxCell", {"id":id_,"parent":"1","vertex":"1","value":str(value),"style":style, **{k:str(v) for k,v in attrs.items()}})
    ET.SubElement(c,"mxGeometry", {"x":fmt(x),"y":fmt(y),"width":fmt(w),"height":fmt(h),"as":"geometry"})
    return id_

def edge(root, source, target):
    e=ET.SubElement(root,"mxCell", {"id":uid("edge",source,target),"parent":"1","edge":"1","source":source,"target":target,"value":"","style":EDGE_STYLE})
    ET.SubElement(e,"mxGeometry", {"relative":"1","as":"geometry"})

def label(root, value, x,y):
    return vertex(root,uid("lbl",value,x,y),value,x,y,58,14,"text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;fontFamily=Courier New;fontSize=10;")

def fmt(v):
    v=float(v)
    return str(int(v)) if v.is_integer() else str(v)

def block_name(t): return str(t).split("-",1)[0].upper() if "-" in str(t) else "OTHER"

def main():
    a=args(); project=load_yaml(a.points)
    if project.get("schema_version") != 4: raise ConfigError(f"schema_version must be 4, got {project.get('schema_version')!r}")
    points=project.get("points")
    if not isinstance(points,dict) or not points: raise ConfigError("points must be a non-empty mapping")
    controllers=project.get("controllers",{})
    layout=project.get("layout",{})
    origin=layout.get("controller_origin",{})
    cx=float(origin.get("x",40)); y=float(origin.get("y",80))
    pitch=float(layout.get("point_pitch_y",96)); gap=float(layout.get("point_vertical_gap",8))
    tx0=float(layout.get("terminal_origin_x",435)); tstep=float(layout.get("terminal_column_spacing",210))
    fx=float(layout.get("field_device_x",1228)); fw=float(layout.get("field_device_width",150))
    blocks={str(k).upper():int(v.get("order",v)) for k,v in layout.get("terminal_blocks",{}).items()}
    default_order=blocks.get("OTHER", max(blocks.values(),default=0)+1)
    def order(t): return blocks.get(block_name(t),default_order)
    def tx(t): return tx0+(order(t)-1)*tstep

    # Controller libraries
    clib={}
    for cname,ccfg in controllers.items():
        p=controller_file(a.repo,ccfg.get("template"),a.controller_library)
        data=load_yaml(p); clib[cname]=data.get("points",data.get("controller",{}).get("points",{}))

    # Validate and determine row positions.
    used={}; rows={}; last_height=None
    for tag,p in points.items():
        if last_height is not None: y += max(pitch,last_height+gap)
        ctrl=p.get("controller"); cp=(p.get("controller_point") or {}).get("point")
        if ctrl not in controllers: raise ConfigError(f"{tag}: unknown controller {ctrl!r}")
        if not cp: raise ConfigError(f"{tag}: controller_point.point is required")
        if cp not in clib[ctrl]: raise ConfigError(f"{tag}: point {cp!r} not found in controller {ctrl}")
        key=(ctrl,cp)
        if key in used: raise ConfigError(f"{tag}: {ctrl}.{cp} already assigned to {used[key]}")
        used[key]=tag
        fdev=p.get("field_device") or {}; interfaces=fdev.get("interfaces") or {}
        if not interfaces: raise ConfigError(f"{tag}: field_device.interfaces is required")
        ordered=sorted(interfaces.items(), key=lambda kv:(int(kv[1].get("order",999)),kv[0]))
        orders=[int(v.get("order",0)) for _,v in ordered]
        if len(set(orders))!=len(orders) or min(orders,default=0)<1: raise ConfigError(f"{tag}: field interface orders must be unique positive integers")
        rows[tag]={"y":y,"interfaces":ordered,"height":ROW_H*len(ordered)}
        last_height=max(64.0,ROW_H*len(ordered))

        declared=set(interfaces)
        for rname,r in (p.get("wiring") or {}).items():
            for k in ("from","via","labels","to"):
                if k not in r: raise ConfigError(f"{tag}.{rname}: missing required {k}")
            if not isinstance(r["via"],list) or not isinstance(r["labels"],list): raise ConfigError(f"{tag}.{rname}: via and labels must be lists")
            if len(r["labels"]) != len(r["via"])+1: raise ConfigError(f"{tag}.{rname}: labels must contain via count + 1 entries")
            src,dst=r["from"],r["to"]
            if set(src) not in ({"point","interface"},{"terminal"}): raise ConfigError(f"{tag}.{rname}: from must be point+interface or terminal")
            if set(dst) not in ({"field_interface"},{"terminal"}): raise ConfigError(f"{tag}.{rname}: to must be field_interface or terminal")
            if "point" in src and src["point"] != cp: raise ConfigError(f"{tag}.{rname}: source point {src['point']} does not match controller_point {cp}")
            if "point" in src and src["interface"] not in clib[ctrl][cp].get("interfaces",{}): raise ConfigError(f"{tag}.{rname}: controller interface {src['interface']!r} not found on {cp}")
            if "field_interface" in dst and dst["field_interface"] not in declared: raise ConfigError(f"{tag}.{rname}: undeclared field_interface {dst['field_interface']!r}")
            terminals=(([src["terminal"]] if "terminal" in src else []) + list(r["via"]) + ([dst["terminal"]] if "terminal" in dst else []))
            os=[order(t) for t in terminals]
            if os != sorted(os):
                suggested=[t for _,t in sorted(enumerate(terminals),key=lambda it:(order(it[1]),it[0]))]
                msg=f"{tag}.{rname}: terminals are not in terminal_blocks order.\n  Current:   {terminals}\n  Suggested route order: {suggested}"
                if a.ignore_terminal_order: print("WARNING: "+msg.replace("\n","\n  "))
                else: raise ConfigError(msg+"\n  Re-run with --ignore-terminal-order to proceed.")

    mx=ET.Element("mxfile",{"host":"Electron"})
    dname=next(iter(controllers.values())).get("drawing_name","Field Connections")
    diag=ET.SubElement(mx,"diagram",{"name":dname,"id":"field-first-v4"})
    model=ET.SubElement(diag,"mxGraphModel",{"dx":"1167","dy":"1591","grid":"1","gridSize":"10","guides":"1","tooltips":"1","connect":"1","arrows":"1","fold":"1","page":"1","pageScale":"1","pageWidth":"1654","pageHeight":"1169","math":"0","shadow":"0"})
    root=ET.SubElement(model,"root"); ET.SubElement(root,"mxCell",{"id":"0"}); ET.SubElement(root,"mxCell",{"id":"1","parent":"0"})

    # Draw points and field devices.
    endpoints={}
    for tag,p in points.items():
        py=rows[tag]["y"]; ctrl=p["controller"]; cp=p["controller_point"]["point"]; pdata=clib[ctrl][cp]; pifs=pdata.get("interfaces",{})
        title=f"<b>{p.get('equipment','')} - {tag}</b><br>{p.get('description','')}"
        vertex(root,sid(ctrl,cp,"point_name"),title,cx,py,207,48,"rounded=0;whiteSpace=wrap;html=1;fontFamily=Courier New;align=left;strokeColor=none;")
        vertex(root,sid(ctrl,cp,"point_type"),pdata.get("display_name",cp),cx+220,py,60,32,"rounded=0;whiteSpace=wrap;html=1;fontStyle=1;fontFamily=Courier New;")
        for iname,idef in pifs.items():
            iy=py if iname in ("signal","supply") else py+32
            if iname not in ("signal","common","supply"): continue
            if iname=="common": vertex(root,sid(ctrl,cp,"secondary_type"),idef.get("display_name","COM"),cx+220,iy,60,32,"rounded=0;whiteSpace=wrap;html=1;fontStyle=1;fontFamily=Courier New;")
            eid=sid(ctrl,cp,iname,"connection")
            vertex(root,eid,idef.get("terminal",""),cx+280,iy,20,32,"rounded=0;whiteSpace=wrap;html=1;points=[[1,0.5,1,0,0]];",object_type="point",point_type=iname.upper())
            endpoints[(tag,"point",iname)]=eid
        ordered=rows[tag]["interfaces"]; dh=rows[tag]["height"]
        vertex(root,sid("dev",tag,"label"),p.get("field_device",{}).get("label",tag),fx+30,py,fw-30,dh,"rounded=0;whiteSpace=wrap;html=1;fontFamily=Courier New;strokeColor=none;")
        for idx,(iname,idef) in enumerate(ordered):
            iid=sid("dev",tag,iname); iy=py+idx*ROW_H
            vertex(root,iid,idef.get("label",iname.upper()),fx,iy,30,32,"rounded=0;whiteSpace=wrap;html=1;fontFamily=Courier New;points=[[0,0.5,1,0,0]];",object_type="interface",interface_type=iname)
            endpoints[(tag,"field",iname)]=iid

    # Draw each explicit route.
    for tag,p in points.items():
        py=rows[tag]["y"]
        for rname,r in (p.get("wiring") or {}).items():
            src,dst=r["from"],r["to"]; via=list(r["via"]); labels=list(r["labels"])
            if "point" in src: current=endpoints[(tag,"point",src["interface"])]
            else:
                t=src["terminal"]; current=vertex(root,sid("term",t,tag,rname,"from"),t,tx(t),py+10,TERM_W,TERM_H,"rounded=0;whiteSpace=wrap;html=1;fontFamily=Courier New;fontSize=10;")
            nodes=[]
            for i,t in enumerate(via):
                ty=py+10
                node=vertex(root,sid("term",t,tag,rname,i),t,tx(t),ty,TERM_W,TERM_H,"rounded=0;whiteSpace=wrap;html=1;fontFamily=Courier New;fontSize=10;")
                nodes.append(node)
            if "field_interface" in dst: final=endpoints[(tag,"field",dst["field_interface"])]
            else:
                t=dst["terminal"]; final=vertex(root,sid("term",t,tag,rname,"to"),t,tx(t),py+10,TERM_W,TERM_H,"rounded=0;whiteSpace=wrap;html=1;fontFamily=Courier New;fontSize=10;")
            chain=[current]+nodes+[final]
            for i,(left,right) in enumerate(zip(chain,chain[1:])):
                edge(root,left,right)
                # labels are centred in the route region rather than attached to edges
                lx=(cx+300+tx0)/2 if i==0 and "point" in src else (tx0+(i+0.5)*tstep)
                if i==len(chain)-2 and "field_interface" in dst: lx=(tx(via[-1]) + TERM_W + fx)/2 if via else (cx+300+fx)/2
                label(root,labels[i],lx-29,py+2)

    ET.indent(mx,space="  ")
    ET.ElementTree(mx).write(a.output,encoding="utf-8",xml_declaration=True)
    print(f"Renderer: {RENDERER_NAME} R{RENDERER_REVISION}")
    print(f"Created {a.output}")

if __name__=="__main__":
    try: main()
    except ConfigError as e:
        print(f"ERROR: {e}",file=sys.stderr); sys.exit(2)
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}",file=sys.stderr); sys.exit(1)
