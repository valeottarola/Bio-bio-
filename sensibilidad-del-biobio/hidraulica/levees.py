"""Levee propagation verified against native HEC-RAS 6.6 (2026-11-02).

Native interpolates station and crest by hydraulic fraction, independently per
side when both ends are active. We deliberately block presence transitions.
"""
import re
import json
import hashlib
from functools import lru_cache
from pathlib import Path
import numpy as np
from .common import require,digest

@lru_cache(maxsize=1)
def _review_records():
    path=Path(__file__).with_name('levee_reviews.json')
    return json.loads(path.read_text(encoding='utf-8'))['intervals'] if path.exists() else []

def inherited_review(a,b):
    """A bounded review of unchanged source records, never a bypass for new XS."""
    from . import geometry_legacy as g
    for row in _review_records():
        if [a['river'],a['reach'],a['station'],b['station']]==row['identity'] and [digest(g.canonical_body(x)) for x in (a,b)]==row['source_body_sha256']:
            return row
    return None

def reviewed_section(a,b,t,lengths,station):
    from . import geometry_legacy as g
    review=inherited_review(a,b)
    if review is None:return None
    check(a,b)
    match=next((item for item in review['templates'] if abs(item['numerator']/item['denominator']-t)<1e-11),None)
    require(match is not None,f"{review['id']}: falta sección nativa 6.6 verificada en fracción {t:.8f}; se conserva el bloqueo.")
    block=match['block']
    require(hashlib.sha256(block.encode()).hexdigest()==match['sha256'],'Sección nativa de contraste alterada')
    xy=g.pairs(block,'#Sta/Elev',8)
    inserted={'block':block,'xy':xy,'station':station}
    for start,end,p in zip(read(a),read(b),read(inserted)):
        if start is None:
            require(p is None,'La plantilla introduce un Levee');continue
        require(end is not None and p is not None,'La plantilla pierde un Levee')
        require(np.max(abs((1-t)*np.array(start)+t*np.array(end)-p))<=.002,'Levee nativo distinto del valor esperado')
        require(clearance(inserted,p)>=-.003001,'Levee nativo bajo terreno en sección nueva')
    return g.length_header(a,lengths,station)+block.split('\n',1)[1]

def read(x):
    lines=re.findall(r'^Levee=(.*)$',x['block'],re.M)
    require(len(lines)<=1,'Más de un registro Levee en '+x['station'])
    if not lines:return [None,None]
    v=[s.strip() for s in lines[0].split(',')]
    require(len(v)>=6 and not any(v[6:]),'Formato/opciones Levee no soportadas: '+x['station'])
    out=[]
    for j in (0,3):
        require(v[j] in ('0','-1'),'Flag Levee no soportado: '+x['station'])
        if v[j]=='0':out.append(None);continue
        station,elevation=float(v[j+1]),float(v[j+2])
        require(np.isfinite([station,elevation]).all(),'Levee no finito')
        require(x['xy'][0,0]-.003<=station<=x['xy'][-1,0]+.003,'Levee fuera del perfil: '+x['station'])
        out.append((station,elevation))
    require(not all(out) or out[0][0]<out[1][0],'Levees izquierdo/derecho invertidos: '+x['station'])
    return out

def clearance(x,p):return float(p[1]-np.interp(p[0],x['xy'][:,0],x['xy'][:,1]))

def check(a,b):
    la,lb=read(a),read(b);issues=[];review=inherited_review(a,b)
    for side,pa,pb in zip(('izquierdo','derecho'),la,lb):
        if (pa is None)!=(pb is None):issues.append(f'Levee {side} aparece/desaparece entre RS {a["station"]} y {b["station"]}; revisar continuidad y comienzo/fin físico, aportar sección o geometría revisada. No se omite ni inventa.')
        if pa is not None and pb is not None:
            for x,p in ((a,pa),(b,pb)):
                if clearance(x,p)<-.003001 and review is None:issues.append(f'Levee {side} RS {x["station"]}: cota {p[1]} bajo terreno en {abs(clearance(x,p)):.4f} m (>3 mm de precisión). Revisar estación/cota en fuente; no se corrige automáticamente.')
    require(not issues,' | '.join(issues))
    return la,lb

def interpolate(a,b,t,xy):
    la,lb=check(a,b);parts=[]
    for side,pa,pb in zip(('izquierdo','derecho'),la,lb):
        if pa is None:parts+=['0','',''];continue
        p=np.round((1-t)*np.array(pa)+t*np.array(pb),6)
        require(xy[0,0]-.003<=p[0]<=xy[-1,0]+.003,'Levee interpolado fuera del perfil')
        gap=float(p[1]-np.interp(p[0],xy[:,0],xy[:,1]))
        require(gap>=-.003001,f'Levee {side} interpolado bajo terreno {abs(gap):.4f} m, fracción {t:.6f}; revisar correspondencia de cordones/topografía. No se eleva automáticamente.')
        parts+=['-1',format(p[0],'.6f').rstrip('0').rstrip('.'),format(p[1],'.6f').rstrip('0').rstrip('.')]
    if all(p is None for p in la):return ''
    return 'Levee='+','.join(parts)+',,\n'

def audit(text,river,spacing=1000,section_only=False):
    from . import geometry as g
    _,rr,_=g.parse(text);_,intervals=g.inventory(text,river)
    by={(x['river'],x['reach'],x['station']):x for r in rr for x in r['xs']};rows=[]
    for it in intervals:
        a=by[(river,it['reach'],it['upstream'])];b=by[(river,it['reach'],it['downstream'])]
        row=dict(it,levees_up=read(a),levees_down=read(b),target_spacing_m=spacing)
        review=inherited_review(a,b)
        row['inherited_source_review']=review['id'] if review else None
        row['source_warning']='Condición Levee/terreno heredada y conservada; revisión limitada a estos cuerpos base. No implica validez física; cada sección nueva se verifica sin excepción.' if review else ''
        if section_only:row['source_warning']='Levees solo en originales. Revisar extensión lateral de flujo en secciones nuevas.'
        try:
            _,ledger,ins=g.generate(text,river,spacing,interval_ids=[it['id']],section_only=section_only);row.update(status='apto_generacion' if ins else 'sin_inserciones',inserted=len(ins),reason='')
        except (ValueError,IndexError) as e:row.update(status='bloqueado',reason=str(e))
        rows.append(row)
    return rows
