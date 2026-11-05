from pathlib import Path
import re,shutil,stat
import numpy as np
from .common import *
from . import geometry as geo
def field(text,name):
    m=re.search(r'^'+re.escape(name)+r'=(.*)$',text,re.M);require(m is not None,'Falta campo '+name);return m[1].strip()
def project(path):
    p=Path(path).resolve();require(p.is_file(),'No existe proyecto: '+str(p));text=geo.read(p);stem=p.stem
    require('SI Units' in text,'Solo unidades SI soportadas')
    plan=p.with_suffix('.'+field(text,'Current Plan'));pt=geo.read(plan)
    require(not re.search(r'^Flow File=u',pt,re.M),'Solo flujo permanente 1D')
    geom=p.with_suffix('.'+field(pt,'Geom File'));flow=p.with_suffix('.'+field(pt,'Flow File'))
    require(all(z.is_file() for z in [plan,geom,flow]),'Faltan archivos de plan/geometría/flujo')
    return dict(prj=p,plan=plan,geom=geom,flow=flow,hdf=Path(str(plan)+'.hdf'),stem=stem)
def flows(text):
    n=int(field(text,'Number of Profiles'));names=[x.strip() for x in field(text,'Profile Names').split(',')]
    require(n==len(names) and len(set(names))==n and len({x[:15] for x in names})==n,'Perfiles duplicados, truncados ambiguos o número inconsistente')
    pat=r'^River Rch & RM=(.*)\n((?:(?![A-Za-z#]).*\n)+)';entries=[]
    for m in re.finditer(pat,text,re.M):
        parts=[x.strip() for x in m[1].split(',')];values=[]
        for line in m[2].splitlines():values += [float(line[i:i+8]) for i in range(0,len(line),8) if line[i:i+8].strip()]
        require(len(values)==n and np.isfinite(values).all() and min(values)>0,'Caudales inválidos en '+str(parts))
        entries.append(dict(river=parts[0],reach=parts[1],station=parts[2],values=values))
    require(entries,'No se encontraron caudales')
    boundaries=[]
    for m in re.finditer(r'^Boundary for River Rch & Prof#=([^\n]*)\n(.*?)(?=^Boundary for River Rch & Prof#=|^DSS Import |\Z)',text,re.M|re.S):
        r,reach,idx=[x.strip() for x in m[1].split(',')];body=m[2];types=re.findall(r'^(?:Up|Dn) Type=\s*(\d+)',body,re.M)
        require(len(types)==2 and all(x in ['0','1','3'] for x in types),'Borde no soportado: solo normal, nivel conocido o interno')
        boundaries.append(dict(river=r,reach=reach,profile=int(idx),body=body))
    require(boundaries,'Faltan condiciones de borde')
    return names,entries,boundaries
def select_flow(text,selected):
    names,entries,boundaries=flows(text);selected=selected or names
    require(len(selected)==len(set(selected)) and set(selected)<=set(names),'Selección de perfiles inválida')
    if selected==names:return text
    idx=[names.index(x) for x in selected];head=text.split('Number of Profiles=')[0]+f'Number of Profiles= {len(selected)}\nProfile Names='+','.join(selected)+'\n'
    for e in entries:
        head+=f'River Rch & RM={e["river"]},{e["reach"]},{e["station"]}\n';tokens=[]
        for i in idx:
            # Preserve the source precision; reject values not representable in eight columns.
            val=e['values'][i];tok=format(val,'.8g');require(len(tok)<=8,'Caudal no representable en ancho 8');tokens.append(tok.rjust(8))
        head+='\n'.join(''.join(tokens[i:i+10]) for i in range(0,len(tokens),10))+'\n'
    for r,reach in dict.fromkeys((b['river'],b['reach']) for b in boundaries):
        bs={b['profile']:b['body'] for b in boundaries if (b['river'],b['reach'])==(r,reach)}
        for new,old in enumerate(idx,1):
            require(old+1 in bs or set(bs)=={1},'Borde ausente en perfil seleccionado')
            body=bs.get(old+1,bs.get(1));head+=f'Boundary for River Rch & Prof#={r},{reach}, {new}\n'+body
    footer=text[text.index('DSS Import '):] if 'DSS Import ' in text else ''
    return head+footer
def verify(prj,river):
    paths=project(prj);gt=geo.read(paths['geom']);sections,intervals=geo.inventory(gt,river);names,entries,boundaries=flows(geo.read(paths['flow']))
    keys={key(x['river'],x['reach'],x['station']) for x in sections};rr={(x['river'],x['reach']) for x in sections}
    require(all(key(e['river'],e['reach'],e['station']) in keys for e in entries),'Entrada de caudal en sección inexistente')
    require(rr=={(b['river'],b['reach']) for b in boundaries},'Bordes no coinciden con Reach')
    require(all(any(key(e['river'],e['reach'],e['station'])==key(x['river'],x['reach'],x['station']) for e in entries) for x in sections if x['chainage_m']==0),'Falta caudal en cabecera de Reach')
    return dict(paths={k:str(v) for k,v in paths.items()},profiles=names,sections=sections,intervals=intervals,flows=entries,boundaries=boundaries,base_count=sum(x['river']==river for x in sections),tributary_count=sum(x['river']!=river for x in sections),reach_count=len({x['reach'] for x in sections if x['river']==river}),hashes={p.name:sha(p) for p in [paths[k] for k in ['prj','plan','geom','flow']]})
def import_model(prj,name,river,provenance=None):
    safe(name);dest=ROOT/'modelos'/name;require(not dest.exists(),'Modelo ya registrado; use otro nombre para nueva versión')
    info=verify(prj,river);paths=project(prj);dest.mkdir(parents=True)
    # Snapshot the complete same-stem model, including historical HDF and binary output.
    for src in paths['prj'].parent.glob(paths['stem']+'.*'):
        if src.is_file():shutil.copy2(src,dest/src.name)
    require(all(sha(dest/n)==h for n,h in info['hashes'].items()),'Copia de entrada inconsistente')
    if provenance:
        known=readjson(provenance)
        for x in info['sections']:x['procedencia']=known.get('|'.join([x['river'],x['reach'],x['station']]),x['procedencia'])
    record=dict(name=name,river=river,project=paths['prj'].name,source=str(paths['prj']),imported_utc=now(),inventory=info)
    record['snapshot_hashes']={p.name:sha(p) for p in dest.iterdir() if p.is_file()};save(dest/'modelo.json',record)
    for p in dest.iterdir():
        if p.is_file() and p.name!='modelo.json':p.chmod(stat.S_IREAD)
    return record

def set_official(name):
    folder,m=load_model(name)
    save(ROOT/'modelo_oficial.json',dict(model=name,source=m['source'],hashes=m['inventory']['hashes'],utc=now()))

def official():
    p=ROOT/'modelo_oficial.json'
    return readjson(p)['model'] if p.exists() else None

def reference_project(folder,m):
    record=ROOT/'referencias_base'/(m['name']+'.json')
    if not record.exists():return folder/m['project']
    r=readjson(record);prj=ROOT/r['project_relative'];p=project(prj)
    require(all(sha(p[k])==m['inventory']['hashes'][p[k].name] for k in ['prj','plan','geom','flow']),'Referencia recalculada no conserva entradas de fuente')
    require(sha(p['hdf'])==r['hdf_sha256'],'HDF de referencia recalculada alterado')
    return prj
def load_model(name):
    folder=ROOT/'modelos'/safe(name);m=readjson(folder/'modelo.json')
    require(all((folder/n).is_file() and sha(folder/n)==h for n,h in m['snapshot_hashes'].items()),'Modelo registrado cambió; registre nueva versión en vez de editar el snapshot')
    return folder,m
