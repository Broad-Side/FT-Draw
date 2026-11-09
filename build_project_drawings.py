#!/usr/bin/env python3
"""Standalone FT-Draw project builder.

No older Python renderer files are required. Reads project points, templates,
project details and revision history, then creates a multi-page Draw.io file.
"""
from __future__ import annotations
import argparse, copy, csv, sys, tempfile, uuid
from datetime import date, datetime
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml

ROOT=Path(__file__).resolve().parent
CTRL_YML=ROOT/'controllerTemplates/SNC25152.yml'; CTRL_DRAW=ROOT/'drawioTemplates/controllers/SNC25152.drawio'
DEV_YML=ROOT/'deviceTemplates'; DEV_DRAW=ROOT/'drawioTemplates/devices'; BG=ROOT/'backgroundTemplates/Background.drawio'
FIELD_X=1001.; MSSB_X=756.; TERM_X=520.; PANEL_X=446.; EXT_X=620.; FIELD_LBL_X=926.; MSSB_LBL_X=697.
LW=50.; LH=16.; GAP=3.; TW=60.; TH=12.; COMMON_DY=20.; TRUNK_X=680.
UI_FIELD_BODY_TOPS={1:86.,2:130.,3:182.,4:226.,5:286.,6:330.,7:382.}
EDGE='edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;endArrow=none;strokeWidth=2;jumpStyle=arc;'
CEDGE=EDGE+'entryX=1;entryY=0.5;entryDx=0;entryDy=0;'


def cli():
 p=argparse.ArgumentParser(); p.add_argument('project',type=Path); p.add_argument('--output',type=Path,default=Path('output.drawio')); p.add_argument('--background',type=Path,default=BG); p.add_argument('--no-revision-prompt',action='store_true'); p.add_argument('--section-name',default='UI Connections'); return p.parse_args()
def yml(p):
 with Path(p).open(encoding='utf-8') as f:return yaml.safe_load(f) or {}
def csvrows(p):
 if not Path(p).exists():return []
 with Path(p).open(newline='',encoding='utf-8-sig') as f:
  return [{(k or '').strip():(v or '').strip() for k,v in r.items()} for r in csv.DictReader(f,skipinitialspace=True)]
def writecsv(p,rows,fields):
 with Path(p).open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore'); w.writeheader(); [w.writerow({k:r.get(k,'') for k in fields}) for r in rows]
def revdate(v):
 for f in ('%Y-%m-%d','%y-%m-%d','%d/%m/%Y','%d/%m/%y'):
  try:return datetime.strptime((v or '').strip(),f).strftime('%y-%m-%d')
  except ValueError:pass
 return (v or '').strip()
def latest(rows):
 rows=[r for r in rows if r.get('Rev')]
 if not rows:return {'Rev':'','Date':'','Description':'','Drafter':'','Reviewer':''}
 try:return max(rows,key=lambda r:int(r['Rev']))
 except ValueError:return rows[-1]
def prompt_rev(path,rows):
 cur=latest(rows); print(f"Current revision: {cur.get('Rev') or 'none'}")
 if input('Create a new revision? [y/N]: ').strip().lower() not in ('y','yes'):return rows
 try:sug=str(int(cur.get('Rev') or 0)+1)
 except ValueError:sug=''
 rev=input(f'Revision number [{sug}]: ').strip() or sug; d=input(f"Revision date [{date.today().strftime('%y-%m-%d')}]: ").strip() or date.today().strftime('%y-%m-%d'); desc=input('Revision description: ').strip(); initials=input('Revision initials: ').strip()
 if not rev or not desc:raise ValueError('Revision number and description are required')
 rows.append({'Rev':rev,'Date':revdate(d),'Description':desc,'Drafter':initials,'Reviewer':initials}); writecsv(path,rows,['Rev','Date','Description','Drafter','Reviewer']); return rows

def load_draw(p):
 t=ET.parse(p); g=t.getroot().find('.//mxGraphModel'); r=t.getroot().find('.//root')
 if g is None or r is None:raise ValueError(f'Invalid Draw.io: {p}')
 return t,g,r
def named(root,stem,suffix):
 a=[p for p in Path(root).rglob('*'+suffix) if p.stem.casefold()==stem.casefold()]
 if len(a)!=1:raise ValueError(f'Expected one {stem}{suffix}, found {a}')
 return a[0]
def obj(root,oid):
 for x in root.iter('object'):
  if x.get('id')==oid:return x
 raise ValueError(f'Object not found: {oid}')
def maybe(root,oid):
 try:return obj(root,oid)
 except ValueError:return None
def cell(e):return e.find('mxCell') if e.tag=='object' else e
def geo(e):
 g=cell(e).find('mxGeometry')
 if g is None:raise ValueError(f'No geometry: {e.get("id")}')
 return g
def bounds(e):
 g=geo(e); return tuple(float(g.get(k,0)) for k in ('x','y','width','height'))
def centre(e):
 x,y,w,h=bounds(e);return x+w/2,y+h/2
def cells(root):return {x.get('id'):x for x in root.iter('mxCell') if x.get('id')}
def absxy(c,cs):
 g=c.find('mxGeometry');x=float(g.get('x',0)) if g is not None else 0;y=float(g.get('y',0)) if g is not None else 0;p=c.get('parent');seen=set()
 while p and p not in ('0','1') and p not in seen:
  seen.add(p);pc=cs.get(p)
  if pc is None:break
  pg=pc.find('mxGeometry')
  if pg is not None:x+=float(pg.get('x',0));y+=float(pg.get('y',0))
  p=pc.get('parent')
 return x,y
def abscentre(e,root):
 c=cell(e);x,y=absxy(c,cells(root));g=c.find('mxGeometry');return x+float(g.get('width',0))/2,y+float(g.get('height',0))/2
def flatten(root,prefix):
 cs=cells(root);out=[];mp={}
 for ch in list(root):
  if ch.tag not in ('object','mxCell'):continue
  c=cell(ch);oid=ch.get('id')
  if c is None or not oid or oid in ('0','1') or c.get('vertex')!='1' or 'group' in c.get('style',''):continue
  z=copy.deepcopy(ch);zc=cell(z);g=zc.find('mxGeometry')
  if g is None:continue
  x,y=absxy(c,cs);g.set('x',str(x));g.set('y',str(y));zc.set('parent','1');nid=f'{prefix}_{oid}';z.set('id',nid);mp[oid]=nid;out.append(z)
 return out,mp
def move(e,dx,dy):
 g=geo(e);g.set('x',str(float(g.get('x',0))+dx));g.set('y',str(float(g.get('y',0))+dy))
def set_id(root,oid,val):
 for o in root.iter('object'):
  if o.get('id')==oid:o.set('label',val);c=o.find('mxCell');c is not None and c.set('value',val);return True
 for c in root.iter('mxCell'):
  if c.get('id')==oid:c.set('value',val);return True
 return False
def description(root,point,equip,tag,desc):
 title=' - '.join(x for x in (equip,tag) if x);v=f'<b>{title}</b><br>{desc}' if title and desc else (f'<b>{title}</b>' if title else desc)
 for oid in (f'{point}_Description',f'{point}Description'):
  if set_id(root,oid,v):break
def text(root,oid,val,x,cy):
 if not val:return
 c=ET.Element('mxCell',{'id':oid,'parent':'1','vertex':'1','value':val,'style':'text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontFamily=Courier New;fontSize=10;'});ET.SubElement(c,'mxGeometry',{'x':str(x),'y':str(cy-LH-GAP+5),'width':str(LW),'height':str(LH),'as':'geometry'});root.append(c)
def terminal_column_x(terminal_id):
 # Terminal blocks step away from the controller in 40 px columns.
 # X4 remains in the existing controller-adjacent position.
 block=(terminal_id or '').strip().upper().split('-',1)[0]
 offsets={'X4':0.,'X3':40.,'X2':80.}
 return TERM_X+offsets.get(block,120.)

def terminal(root,oid,val,cy):
 tx=terminal_column_x(val)
 c=ET.Element('mxCell',{'id':oid,'parent':'1','vertex':'1','value':val,'style':'rounded=0;whiteSpace=wrap;html=1;fontSize=10;'})
 ET.SubElement(c,'mxGeometry',{'x':str(tx),'y':str(cy-TH/2),'width':str(TW),'height':str(TH),'as':'geometry'})
 root.append(c)
def edge(root,oid,src,dst,style,pts=None):
 c=ET.Element('mxCell',{'id':oid,'parent':'1','edge':'1','source':src,'target':dst,'value':'','style':style});g=ET.SubElement(c,'mxGeometry',{'relative':'1','as':'geometry'})
 if pts:
  g.set('x','-0.039');a=ET.SubElement(g,'Array',{'as':'points'});[ET.SubElement(a,'mxPoint',{'x':str(x),'y':str(y)}) for x,y in pts]
 root.append(c)
def boundary(root,oid,x):
 c=ET.Element('mxCell',{'id':oid,'parent':'1','edge':'1','value':'','style':'endArrow=none;dashed=1;html=1;rounded=0;defaultLabelBackgroundColor=page;'});g=ET.SubElement(c,'mxGeometry',{'relative':'1','as':'geometry'});ET.SubElement(g,'mxPoint',{'x':str(x),'y':'925','as':'sourcePoint'});ET.SubElement(g,'mxPoint',{'x':str(x),'y':'25','as':'targetPoint'});root.append(c)

def route(root,prefix,role,src,dst,dstobj,row,mssb,shared=False,reg=None):
 so=obj(root,src);_,sy=centre(so);_,ty=abscentre(dstobj,root);mode=row.get(role+'_terminal_mode','').lower();term=row.get(role+'_terminal_id','');panel=row.get(role+'_wire','');ext=row.get(role+'_core','')
 if role=='common' and shared:
  state=reg.get(dst);first=state is None
  if first:
   tid=f'shared_common_terminal_{dst}';terminal(root,tid,term,ty);edge(root,f'shared_common_panel_{dst}',tid,dst,CEDGE);text(root,f'shared_common_panel_{dst}_label',panel,PANEL_X,ty);state={'tid':tid,'ext':ext,'ty':ty};reg[dst]=state
  ext=state['ext'] or ext;pts=[(TRUNK_X,sy),(TRUNK_X,state['ty'])] if abs(sy-state['ty'])>.1 else None;edge(root,f'edge_{prefix}_common_external',src,state['tid'],CEDGE,pts)
  if first:text(root,f'edge_{prefix}_common_external_label',ext,EXT_X,state['ty'])
  text(root,f'edge_{prefix}_common_end_label',ext,MSSB_LBL_X if mssb else FIELD_LBL_X,sy);return
 style=CEDGE if role=='common' else EDGE
 if mode not in ('terminal','mssb') or not term:
  edge(root,f'edge_{prefix}_{role}',src,dst,style)
  text(root,f'edge_{prefix}_{role}_label',panel or ext,PANEL_X,ty)
  return
 # Controller-side terminals and both left cable-label columns are anchored to
 # the controller point row. Field-device placement must never move them.
 tid=f'terminal_{prefix}_{role}'
 terminal(root,tid,term,ty)
 edge(root,f'edge_{prefix}_{role}_external',src,tid,EDGE)
 text(root,f'edge_{prefix}_{role}_external_label',ext,EXT_X,ty)
 # The endpoint label remains anchored to the actual field-device connector.
 text(root,f'edge_{prefix}_{role}_end_label',ext,MSSB_LBL_X if mssb else FIELD_LBL_X,sy)
 edge(root,f'edge_{prefix}_{role}_panel',tid,dst,style)
 text(root,f'edge_{prefix}_{role}_panel_label',panel,PANEL_X,ty)

def render_points(points,outfile):
 ctrl=yml(CTRL_YML);tree,_,root=load_draw(CTRL_DRAW);rows=csvrows(points);counts={};reg={}
 for r in rows:
  p=r.get('controller_point','')
  if p in ctrl.get('points',{}):c=ctrl['points'][p]['interfaces']['common']['drawio_id'];counts[c]=counts.get(c,0)+1
 built=0;mssbline=False
 for n,r in enumerate(rows,1):
  point=r.get('controller_point','');tmpl=r.get('device_template','');tag=r.get('point_tag') or f'POINT-{n}'
  if not point or not tmpl:continue
  pd=ctrl['points'][point];st=pd['interfaces']['signal']['drawio_id'];cc=pd['interfaces']['common']['drawio_id'];ct=maybe(root,'z'+cc) or obj(root,cc);ctid=ct.get('id');so=obj(root,st)
  dy=yml(named(DEV_YML,tmpl,'.yml'));_,_,dr=load_draw(named(DEV_DRAW,tmpl,'.drawio'));ints=dy['device_template']['drawing']['interfaces'];ids={k:v.get('drawio_id',v.get('id',k)) for k,v in ints.items()};pre=f'd{n}_{uuid.uuid4().hex[:8]}';leaves,mp=flatten(dr,pre);ss=mp[ids['signal']];cs=mp[ids['common']];tmp=ET.Element('root');[tmp.append(x) for x in leaves];sl=obj(tmp,ss);cl=obj(tmp,cs);mssb=r.get('destination_type','').lower()=='mssb' or r.get('signal_terminal_mode','').lower()=='mssb'
  if mssb and not mssbline:boundary(root,'mssb_boundary_right',918);mssbline=True
  _,ty=abscentre(so,root);sx,sy=centre(sl);dx=(MSSB_X if mssb else FIELD_X)+bounds(sl)[2]/2-sx;ddy=ty-sy
  [move(x,dx,ddy) for x in leaves]
  # Field-device placement repeats the proven UI1-UI7 pattern for UI8-UI14.
  # This deliberately ignores the intervening +15 V controller row, which
  # otherwise offsets and interleaves the right-side device bodies.
  if not mssb and point.upper().startswith('UI'):
   try:
    ui_number=int(point.upper().replace('UI',''))
   except ValueError:
    ui_number=0
   if 1<=ui_number<=14:
    pattern_number=((ui_number-1)%7)+1
    page_offset=400. if ui_number>=8 else 0.
    desired_body_top=UI_FIELD_BODY_TOPS[pattern_number]+page_offset
    body_candidates=[]
    for leaf in leaves:
     c=cell(leaf);x,y,w,h=bounds(leaf)
     if c.get('vertex')=='1' and w>=80 and h>=30:
      body_candidates.append((w*h,leaf,y))
    if body_candidates:
     _,body,current_body_top=max(body_candidates,key=lambda item:item[0])
     body_shift=desired_body_top-current_body_top
     [move(leaf,0,body_shift) for leaf in leaves]
  sl=obj(tmp,ss);cl=obj(tmp,cs);_,sigy=centre(sl)
  # Use the identical connector rule for every UI point. The whole UI8-UI14
  # device has already been placed on the repeated lower-page body pattern,
  # which accounts for the intervening +15 V row. From this point onward the
  # same UI1-UI7 rule applies: leave the signal interface where the template
  # placed it, then put the common interface exactly 20 px above or below it
  # according to the controller common-row relationship.
  _,comy=abscentre(ct,root)
  common_geometry=geo(cl)
  common_geometry.set(
   'y',
   str(sigy+(COMMON_DY if sigy<comy else -COMMON_DY)-float(common_geometry.get('height',0))/2),
  )
  for x in leaves:
   c=cell(x)
   if tmpl in c.get('value',''):c.set('value',f'<span style="font-family: &quot;Courier New&quot;;">{tag}</span>')
  if r.get('signal_destination_terminal'):sl.set('label',r['signal_destination_terminal'])
  if r.get('common_destination_terminal'):cl.set('label',r['common_destination_terminal'])
  [root.append(x) for x in leaves];description(root,point,r.get('equipment',''),tag,r.get('description',''));route(root,pre,'signal',ss,st,so,r,mssb);route(root,pre,'common',cs,ctid,ct,r,mssb,counts.get(cc,0)>1,reg);built+=1
 if not built:raise ValueError('No points built')
 ET.indent(tree,space='  ');tree.write(outfile,encoding='utf-8',xml_declaration=True)

def descendants(root,parent):
 kids={}
 for c in root.iter('mxCell'):kids.setdefault(c.get('parent',''),[]).append(c)
 out=[];stack=[parent]
 while stack:
  p=stack.pop()
  for c in kids.get(p,[]):
   if c.get('id'):stack.append(c.get('id'))
   g=c.find('mxGeometry')
   if c.get('vertex')=='1' and g is not None and 'group' not in c.get('style','') and float(g.get('height',0))>10:out.append((float(g.get('width',0))*float(g.get('height',0)),c))
 return out
def title(root,oid,val):
 for o in root.iter('object'):
  if o.get('id')!=oid:continue
  c=o.find('mxCell')
  if c is not None and 'group' not in c.get('style',''):o.set('label',val);c.set('value',val);return True
  a=descendants(root,oid)
  if a:max(a,key=lambda x:x[0])[1].set('value',val)
  return True
 return set_id(root,oid,val)
def revision(root,r):
 title(root,'Rev_Number',r.get('Rev',''));title(root,'Rev_Number_History',r.get('Rev',''));title(root,'Rev_Details',r.get('Description',''));vals={1440:revdate(r.get('Date','')),1480:r.get('Description',''),1620:r.get('Reviewer') or r.get('Drafter','')}
 for c in root.iter('mxCell'):
  g=c.find('mxGeometry')
  if g is None or c.get('parent')!='1' or abs(float(g.get('y',-99))-797)>1:continue
  for x,v in vals.items():
   if abs(float(g.get('x',-99))-x)<1:c.set('value',v)
def main():
 a=cli();inp=a.project.resolve();pdir=inp.parent if inp.suffix.lower()=='.csv' else inp;points=inp if inp.suffix.lower()=='.csv' else pdir/'points.csv';details=yml(pdir/'panelDetails.yml');details=details.get('Project',details);revs=csvrows(pdir/'revHistory.csv')
 if not a.no_revision_prompt and sys.stdin.isatty():revs=prompt_rev(pdir/'revHistory.csv',revs)
 rev=latest(revs);rows=csvrows(points);groups={}
 for r in rows:groups.setdefault(r.get('controller_id') or 'UNASSIGNED',[]).append(r)
 mx=ET.Element('mxfile',{'host':'Electron'})
 with tempfile.TemporaryDirectory() as td:
  td=Path(td)
  for i,(cid,rs) in enumerate(groups.items(),1):
   pc=td/f'{cid}.csv';body=td/f'{cid}.drawio';writecsv(pc,rs,list(rows[0]));render_points(pc,body);_,graph,br=load_draw(a.background);_,_,rr=load_draw(body)
   for k in ('Project','Client','Vendor','Drafter_Company','Drawing_Set','Designer','Drafter','Checked'):title(br,k,str(details.get(k,'')))
   title(br,'Drawing_Name',f'{cid} - {a.section_name}');revision(br,rev)
   for ch in list(rr):
    if ch.get('id') not in ('0','1'):br.append(copy.deepcopy(ch))
   d=ET.SubElement(mx,'diagram',{'name':f'{cid} - {a.section_name}','id':f'page-{i}-{cid}'});d.append(copy.deepcopy(graph))
 ET.indent(mx,space='  ');a.output.parent.mkdir(parents=True,exist_ok=True);ET.ElementTree(mx).write(a.output,encoding='utf-8',xml_declaration=True);print(f'Created {a.output} with {len(groups)} page(s)')
if __name__=='__main__':
 try:main()
 except Exception as e:print(f'ERROR: {e}',file=sys.stderr);raise SystemExit(1)
