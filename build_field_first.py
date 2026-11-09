#!/usr/bin/env python3
"""Field-first Draw.io test renderer.

Builds generic controller-point modules, field-device symbols, ordered terminal
paths and segment labels from points.yml. Designed for the initial UI1/UI2 test.
"""
from __future__ import annotations
import argparse, copy, sys, uuid
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml

EDGE='edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;endArrow=none;strokeWidth=2;jumpStyle=arc;'

def arguments():
 p=argparse.ArgumentParser();p.add_argument('points_yml',type=Path);p.add_argument('--output',type=Path,default=Path('field_first_test.drawio'));p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent);return p.parse_args()
def load_yaml(p):
 with p.open(encoding='utf-8') as f:return yaml.safe_load(f) or {}
def find_named(root,stem,suffix):
 hits=[p for p in root.rglob('*'+suffix) if p.stem.casefold()==stem.casefold()]
 if len(hits)!=1:raise ValueError(f'Expected one {stem}{suffix} under {root}, found {hits}')
 return hits[0]
def load_graph(p):
 t=ET.parse(p);m=t.getroot().find('.//mxGraphModel');r=t.getroot().find('.//root')
 if m is None or r is None:raise ValueError(f'Invalid Draw.io file: {p}')
 return t,m,r
def find_controller_point_template(root, configured_name):
 # Prefer the configured filename, but fall back to XML object-ID discovery so
 # the template may have any sensible filename.
 stem=Path(configured_name).stem
 direct=list((root/'drawioTemplates/controllers').rglob(stem+'.drawio'))
 if len(direct)==1:
  return direct[0]
 required={'point_type','point_secondary_type','point_name','primary_connection','secondary_connection'}
 matches=[]
 for candidate in (root/'drawioTemplates/controllers').rglob('*.drawio'):
  try:
   _,_,graph_root=load_graph(candidate)
   ids={o.get('id') for o in graph_root.iter('object') if o.get('id')}
   if required.issubset(ids):matches.append(candidate)
  except Exception:
   continue
 if len(matches)==1:return matches[0]
 if not matches:
  raise ValueError(
   'No generic two-connection controller template found. Expected either '
   f'{stem}.drawio or a .drawio file containing IDs: {sorted(required)}'
  )
 raise ValueError(f'Multiple generic controller templates found: {matches}')

def cell(e):return e.find('mxCell') if e.tag=='object' else e
def geom(e):
 g=cell(e).find('mxGeometry')
 if g is None:raise ValueError(f'Missing geometry for {e.get("id")}')
 return g
def objects(root):return {o.get('id'):o for o in root.iter('object') if o.get('id')}
def mx_cells(root):return {c.get('id'):c for c in root.iter('mxCell') if c.get('id')}
def abs_xy(c,cells):
 g=c.find('mxGeometry');x=float(g.get('x',0)) if g is not None else 0;y=float(g.get('y',0)) if g is not None else 0;p=c.get('parent');seen=set()
 while p and p not in ('0','1') and p not in seen:
  seen.add(p);pc=cells.get(p)
  if pc is None:break
  pg=pc.find('mxGeometry')
  if pg is not None:x+=float(pg.get('x',0));y+=float(pg.get('y',0))
  p=pc.get('parent')
 return x,y
def flatten(root,prefix,dx=0,dy=0):
 cs=mx_cells(root);out=[];idmap={}
 for ch in list(root):
  if ch.tag not in ('object','mxCell'):continue
  c=cell(ch);oid=ch.get('id')
  if not oid or oid in ('0','1') or c is None or c.get('vertex')!='1' or 'group' in c.get('style',''):continue
  z=copy.deepcopy(ch);zc=cell(z);g=zc.find('mxGeometry');x,y=abs_xy(c,cs);g.set('x',str(x+dx));g.set('y',str(y+dy));zc.set('parent','1');nid=f'{prefix}_{oid}';z.set('id',nid);idmap[oid]=nid;out.append(z)
 return out,idmap
def set_value(e,value):
 if e.tag=='object':e.set('label',value)
 c=cell(e);c.set('value',value)
def add_terminal(root,oid,label,x,y):
 c=ET.Element('mxCell',{'id':oid,'parent':'1','vertex':'1','value':label,'style':'rounded=0;whiteSpace=wrap;html=1;fontFamily=Courier New;fontSize=10;'});ET.SubElement(c,'mxGeometry',{'x':str(x),'y':str(y-6),'width':'60','height':'12','as':'geometry'});root.append(c)
def add_label(root,oid,label,x,y):
 if not label:return
 c=ET.Element('mxCell',{'id':oid,'parent':'1','vertex':'1','value':label,'style':'text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;fontFamily=Courier New;fontSize=10;'});ET.SubElement(c,'mxGeometry',{'x':str(x),'y':str(y-14),'width':'58','height':'14','as':'geometry'});root.append(c)
def add_edge(root,oid,source,target):
 c=ET.Element('mxCell',{'id':oid,'parent':'1','edge':'1','source':source,'target':target,'value':'','style':EDGE});ET.SubElement(c,'mxGeometry',{'relative':'1','as':'geometry'});root.append(c)
def terminal_id(node):return node.get('terminal')
def block_name(tid):return tid.split('-',1)[0].upper()
def controller_point_node(nodes):
 x=nodes[-1]
 if 'controller' not in x:raise ValueError('Last node must be a controller endpoint')
 return x
def validate(project,controllers,devices):
 for tag,d in project['devices'].items():
  dt=devices[d['template']]['device_template'];required={k for k,v in dt['drawing']['interfaces'].items() if v.get('required')}
  missing=required-set(d.get('connections',{}))
  if missing:raise ValueError(f'{tag}: missing interfaces {sorted(missing)}')
  for name,c in d['connections'].items():
   if len(c['segments'])!=len(c['nodes'])-1:raise ValueError(f'{tag}.{name}: segments must equal nodes minus one')
   ep=controller_point_node(c['nodes']);ct=controllers[project['controllers'][ep['controller']]['template']];point=ct['points'][ep['point']]
   if point['point_type'] not in dt['compatible_point_types']:raise ValueError(f'{tag}: {dt["name"]} not compatible with {ep["point"]}')
   if ep['interface'] not in point['interfaces']:raise ValueError(f'{tag}.{name}: controller interface not found')
def main():
 a=arguments();rootdir=a.root.resolve();project=load_yaml(a.points_yml.resolve())
 controllers={};
 for cid,cfg in project['controllers'].items():
  name=cfg['template'];controllers[name]=load_yaml(find_named(rootdir/'controllerTemplates',name,'.yml'))
 devices={}
 for d in project['devices'].values():
  name=d['template'];devices[name]=load_yaml(find_named(rootdir/'deviceTemplates',name,'.yml'))
 validate(project,controllers,devices)
 mx=ET.Element('mxfile',{'host':'Electron'});diagram=ET.SubElement(mx,'diagram',{'name':'Field First Test','id':'field-first-test'});model=ET.SubElement(diagram,'mxGraphModel',{'grid':'1','gridSize':'10','page':'1','pageWidth':'1654','pageHeight':'1169'});out=ET.SubElement(model,'root');ET.SubElement(out,'mxCell',{'id':'0'});ET.SubElement(out,'mxCell',{'id':'1','parent':'0'})
 layout=project['layout'];cx=float(layout['controller_origin']['x']);cy=float(layout['controller_origin']['y']);pitch=float(layout['point_pitch_y']);fx=float(layout['field_device_x']);fp=float(layout['field_device_pitch_y']);tx=float(layout['terminal_origin_x']);spacing=float(layout['terminal_column_spacing']);orders={k.upper():v['order'] for k,v in layout['terminal_blocks'].items()}
 point_instances={};device_instances={}
 used=[]
 for d in project['devices'].values():
  for c in d['connections'].values():
   ep=controller_point_node(c['nodes']);key=(ep['controller'],ep['point'])
   if key not in used:used.append(key)
 configured_template=next(iter(project['controllers'].values())).get('point_template','generic_controller_point_2_connection.drawio');pt_file=find_controller_point_template(rootdir,configured_template);print(f'Using controller point template: {pt_file}');_,_,pt_root=load_graph(pt_file)
 for i,(cid,pid) in enumerate(used):
  ctrl=controllers[project['controllers'][cid]['template']];pd=ctrl['points'][pid];leaves,mp=flatten(pt_root,f'{cid}_{pid}',cx-70,cy+i*pitch-60);lookup={e.get('id'):e for e in leaves}
  primary_name=next(iter(pd['interfaces']));secondary_name=list(pd['interfaces'])[1] if len(pd['interfaces'])>1 else None;primary=pd['interfaces'][primary_name];secondary=pd['interfaces'].get(secondary_name,{}) if secondary_name else {}
  set_value(lookup[mp['point_type']],pd.get('display_name',pid));set_value(lookup[mp['point_secondary_type']],secondary.get('display_name',secondary_name or ''));set_value(lookup[mp['primary_connection']],str(primary.get('terminal','')));set_value(lookup[mp['secondary_connection']],str(secondary.get('terminal','')))
  for e in leaves:out.append(e)
  point_instances[(cid,pid,primary_name)]=mp['primary_connection']
  if secondary_name:point_instances[(cid,pid,secondary_name)]=mp['secondary_connection']
 # device symbols and descriptions
 for i,(tag,d) in enumerate(project['devices'].items()):
  dt=devices[d['template']]['device_template'];df=find_named(rootdir/'drawioTemplates/devices',Path(dt['drawing']['template']).stem,'.drawio');_,_,dr=load_graph(df);leaves,mp=flatten(dr,f'dev_{tag}',fx-640,cy+i*fp-290);lookup={e.get('id'):e for e in leaves};set_value(lookup[mp[dt['drawing']['label_id']]],tag)
  for e in leaves:out.append(e)
  device_instances[tag]={name:mp[cfg['drawio_id']] for name,cfg in dt['drawing']['interfaces'].items()}
  # write description into controller point module
  first=next(iter(d['connections'].values()));ep=controller_point_node(first['nodes']);desc_id=f"{ep['controller']}_{ep['point']}_point_name";target=next((e for e in out.iter() if e.get('id')==desc_id),None)
  if target is not None:set_value(target,f"<b>{d.get('equipment','')} - {tag}</b><br>{d.get('description','')}")
 # routes
 shared_terminals={}
 for tag,d in project['devices'].items():
  for iface,cfg in d['connections'].items():
   nodes=list(reversed(cfg['nodes']));segments=list(reversed(cfg['segments']));ep=nodes[0];source=point_instances[(ep['controller'],ep['point'],ep['interface'])];prev=source
   for j,node in enumerate(nodes[1:-1]):
    tid=terminal_id(node);block=block_name(tid);order=orders.get(block,orders.get('OTHER',4));key=tid
    if key in shared_terminals:target=shared_terminals[key]
    else:
     target=f'term_{tid.replace("-","_")}';add_terminal(out,target,tid,tx+(order-1)*spacing,cy+used.index((ep['controller'],ep['point']))*pitch+16);shared_terminals[key]=target
    add_edge(out,f'edge_{uuid.uuid4().hex[:10]}',prev,target);add_label(out,f'label_{uuid.uuid4().hex[:10]}',segments[j].get('label',''),tx+(order-1)*spacing-64,cy+used.index((ep['controller'],ep['point']))*pitch+16);prev=target
   field=device_instances[tag][iface];add_edge(out,f'edge_{uuid.uuid4().hex[:10]}',prev,field);last=segments[-1];add_label(out,f'label_{uuid.uuid4().hex[:10]}',last.get('label',''),fx-75,cy+list(project['devices']).index(tag)*fp+16)
 ET.indent(mx,space='  ');a.output.parent.mkdir(parents=True,exist_ok=True);ET.ElementTree(mx).write(a.output,encoding='utf-8',xml_declaration=True);print(f'Created {a.output}')
if __name__=='__main__':
 try:main()
 except Exception as e:print(f'ERROR: {e}',file=sys.stderr);raise SystemExit(1)
