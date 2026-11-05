"""Conservative string-model interpolation. Source records are never edited in place."""
import re,math
import numpy as np
from . import geometry_legacy as g
from .common import require,digest,ValidationError,key
from . import levees
read=g.read
parse=g.parse
write=g.write
def manning(x):
    lines=x['block'].splitlines();i=next(i for i,l in enumerate(lines) if l.startswith('#Mann='));head=lines[i].split('=')[1].split(',')
    require(head[1].strip()=='-1','Manning vertical/variable no soportado; importar interpolación nativa verificada.')
    n=int(head[0]);v=[]
    for line in lines[i+1:]:
        if '=' in line or not line.strip():break
        v.extend(float(line[j:j+8]) for j in range(0,len(line),8) if line[j:j+8].strip())
        if len(v)>=3*n:break
    a=np.array(v).reshape(-1,3);require(len(a)==n and np.all(a[:,1]>0) and np.all(np.diff(a[:,0])>0),'Manning inválido')
    require(np.all(a[:,2]==0),'Flags especiales de Manning no soportados')
    require(a[0,0]<=x['xy'][0,0]+.001 and a[-1,0]<=x['xy'][-1,0]+.001,'Transiciones de Manning fuera del perfil')
    return a
def inventory(text,river):
    prefix,reaches,footer=parse(text);sections=[];intervals=[]
    for r in reaches:
        chain=0.
        for i,x in enumerate(r['xs']):
            ma=manning(x);lv=levees.read(x);require(np.isfinite(x['xy']).all() and min(x['lengths'])>=0,'Geometría no finita o longitudes negativas')
            require(x['xy'][0,0]<=x['banks'][0]<x['banks'][1]<=x['xy'][-1,0],'Bancos fuera del perfil')
            sections.append(dict(river=r['river'],reach=r['reach'],station=x['station'],chainage_m=chain,base=True,procedencia='marcada_interpolada_en_fuente' if x['station'].endswith('*') else 'base_usuario_procedencia_no_documentada',manning_breaks=len(ma),body_sha256=digest(g.canonical_body(x))))
            if i<len(r['xs'])-1:
                require(x['lengths'][1]>0,'Longitud interna de cauce no positiva')
                if r['river']==river:
                    y=r['xs'][i+1];iid=f'I{len(intervals)+1:03d}'
                    intervals.append(dict(id=iid,river=river,reach=r['reach'],upstream=x['station'],downstream=y['station'],length_m=x['lengths'][1]))
            sections[-1]['levees']=lv
            sections[-1]['levee_record']=bool(re.search(r'^Levee=',x['block'],re.M))
            sections[-1]['levee_clearance_m']=[levees.clearance(x,p) if p else None for p in lv]
            chain+=x['lengths'][1]
    require(intervals,'No hay intervalos del río seleccionado')
    return sections,intervals
def roughness(a,b,t):
    """Four master cords, union of breakpoints in each region, right-continuous n."""
    aa=np.r_[a['xy'][0,0],a['banks'],a['xy'][-1,0]];bb=np.r_[b['xy'][0,0],b['banks'],b['xy'][-1,0]]
    ma,mb=manning(a),manning(b);rows=[]
    def value(m,s):return m[max(0,np.searchsorted(m[:,0],s+1e-8,side='right')-1),1]
    for i in range(3):
        da,db=aa[i+1]-aa[i],bb[i+1]-bb[i]
        if da==db==0:continue
        require(da>0 and db>0,'Región de rugosidad degenerada en un solo extremo')
        ua=(ma[:,0]-aa[i])/da;ub=(mb[:,0]-bb[i])/db
        us=np.unique(np.r_[0,ua[(ua>0)&(ua<1)],ub[(ub>0)&(ub<1)]])
        for u in us:
            xa=aa[i]+u*da;xb=bb[i]+u*db
            rows.append(((1-t)*xa+t*xb,(1-t)*value(ma,xa)+t*value(mb,xb),0))
    arr=np.array(rows);arr[:,0]=np.round(arr[:,0],3);arr[:,1]=np.round(arr[:,1],6)
    require(np.all(np.diff(arr[:,0])>0),'Transiciones colapsan a precisión de archivo')
    return arr
def restriction_check(a,b,section_only=False):
    allowed={'Type RM Length L Ch R ','Node Name','XS GIS Cut Line','Node Last Edited Time','#Sta/Elev','#Mann','Bank Sta','XS Rating Curve','XS HTab Starting El and Incr','XS HTab Horizontal Distribution','Exp/Cntr','Levee'}
    for x in [a,b]:
        unknown={l.split('=')[0] for l in x['block'].splitlines() if '=' in l}-allowed
        require(not unknown,'Intervalo requiere interpolador nativo/revisión: '+', '.join(sorted(unknown)))
        require(g.field(x['block'],'XS Rating Curve').replace(' ','')=='0,0','Curva de gasto de sección no soportada')
    if not section_only:levees.check(a,b)
def cutline(x,n):
    p=g.pairs(x['block'],'XS GIS Cut Line',16);dist=np.r_[0,np.cumsum(np.linalg.norm(np.diff(p,axis=0),axis=1))]
    require(dist[-1]>0,'Corte GIS degenerado');u=np.linspace(0,dist[-1],n)
    return np.column_stack([np.interp(u,dist,p[:,i]) for i in [0,1]])
def identifier(a,b,t,used):
    val=(1-t)*a['rm']+t*b['rm']
    for precision in range(6,-1,-1):
        rs=f'{val:.{precision}f}'.rstrip('0').rstrip('.') if precision else str(round(val))
        rs+='*'
        if len(rs)<=8 and b['rm']<float(rs[:-1])<a['rm'] and key(a['river'],a['reach'],rs) not in used:
            used.add(key(a['river'],a['reach'],rs));return rs
    raise ValidationError('No se puede asignar RS único en 8 caracteres. Cambie resolución o identificadores del modelo base explícitamente.')
def new_section(a,b,t,lengths,station,section_only=False):
    restriction_check(a,b,section_only)
    reviewed=None if section_only else levees.reviewed_section(a,b,t,lengths,station)
    if reviewed is not None:return reviewed
    aa,bb=g.anchors(a),g.anchors(b);points=[]
    for i in range(4):
        da,db=aa[i+1]-aa[i],bb[i+1]-bb[i]
        require(da>=0 and db>=0,'Cordones invertidos')
        if da==db==0:continue
        ua=(a['xy'][:,0]-aa[i])/da if da else np.array([]);ub=(b['xy'][:,0]-bb[i])/db if db else np.array([])
        us=np.unique(np.r_[0,1,ua[(ua>0)&(ua<1)],ub[(ub>0)&(ub<1)]])
        xa=aa[i]+us*da;xb=bb[i]+us*db
        points.extend(zip((1-t)*xa+t*xb,(1-t)*np.interp(xa,a['xy'][:,0],a['xy'][:,1])+t*np.interp(xb,b['xy'][:,0],b['xy'][:,1])))
    raw=np.round(np.array(points),3);points=[]
    for x in np.unique(raw[:,0]):
        zz=raw[raw[:,0]==x,1];require(np.ptp(zz)<=.003001,'Colapso de cordones con salto vertical >3 mm; revisión necesaria');points.append((x,float(zz.min())))
    xy=np.array(points);require(len(xy)<=500,'Más de 500 coordenadas; no simplificar automáticamente')
    banks=np.round((1-t)*np.array(a['banks'])+t*np.array(b['banks']),3)
    require(all(np.any(abs(xy[:,0]-v)<1e-8) for v in banks),'Banco sin coordenada coincidente')
    n=roughness(a,b,t);nc=max(len(g.pairs(a['block'],'XS GIS Cut Line',16)),len(g.pairs(b['block'],'XS GIS Cut Line',16)))
    cuts=(1-t)*cutline(a,nc)+t*cutline(b,nc)
    coeff=(1-t)*np.array(list(map(float,g.field(a['block'],'Exp/Cntr').split(','))))+t*np.array(list(map(float,g.field(b['block'],'Exp/Cntr').split(','))))
    step=min(float(g.field(x['block'],'XS HTab Starting El and Incr').split(',')[1]) for x in [a,b]);require(step>0,'Paso HTab inválido')
    start=float(xy[:,1].min())+.15;count=max(20,math.ceil((xy[:,1].max()-start)/step)+10)
    require(count<=500,'HTab supera 500 niveles; revisión nativa necesaria')
    levee_line='' if section_only else levees.interpolate(a,b,t,xy)
    tokens=[]
    for row in n:
        tokens.append(''.join(f'{v:.6f}'.rstrip('0').rstrip('.').rjust(8) for v in row))
    require(all(len(tok)==24 for tok in tokens),'Manning excede ancho fijo')
    return (g.length_header(a,lengths,station)+f'Node Name=INTERP {a["station"]}/{b["station"]}\nXS GIS Cut Line={nc}\n'+g.fixed_pairs(cuts,16,3,2)+f'#Sta/Elev= {len(xy)}\n'+g.fixed_pairs(xy)+f'#Mann= {len(n)} ,-1,0\n'+'\n'.join(''.join(tokens[i:i+3]) for i in range(0,len(tokens),3))+'\nBank Sta='+','.join(map(str,banks))+'\n'+levee_line+f'XS Rating Curve= 0 ,0\nXS HTab Starting El and Incr={start:.3f},{step},{count}\nXS HTab Horizontal Distribution= '+g.field(a['block'],'XS HTab Horizontal Distribution')+'\nExp/Cntr='+','.join(f'{v:.6f}' for v in coeff)+'\n\n')
def generate(text,river,spacing,reaches=None,interval_ids=None,overrides=None,section_only=False):
    require(np.isfinite(spacing) and spacing>0,'Espaciamiento debe ser positivo y finito')
    sections,intervals=inventory(text,river);interval_ids=interval_ids or [];reaches=reaches or [];overrides=overrides or {}
    require(set(interval_ids)<=set(x['id'] for x in intervals),'ID de intervalo inexistente')
    require(set(reaches)<=set(x['reach'] for x in intervals),'Reach inexistente')
    require(set(overrides)<=set(x['id'] for x in intervals),'Override de intervalo inexistente')
    lookup={(x['river'],x['reach'],x['upstream']):x for x in intervals}
    prefix,rr,footer=parse(text);out=prefix;ledger=[];inserted=[];blocked=[]
    used={key(x['river'],x['reach'],x['station']) for r in rr for x in r['xs']}
    for r in rr:
        out+=r['header']
        for i,x in enumerate(r['xs']):
            it=lookup.get((r['river'],r['reach'],x['station']));selected=it and (not reaches or r['reach'] in reaches) and (not interval_ids or it['id'] in interval_ids)
            h=float(overrides.get(it['id'],spacing)) if it else spacing;require(np.isfinite(h) and h>0,'Espaciamiento local inválido')
            n=math.ceil(x['lengths'][1]/h) if selected else 1;n=max(1,n)
            require(n<=5000,'Más de 5000 segmentos en un intervalo; revise configuración')
            if n>1:
                try:
                    b=r['xs'][i+1];restriction_check(x,b,section_only);segments=np.repeat(np.array(x['lengths'])[None,:]/n,n,axis=0);segments=np.round(segments,6);segments[-1]=np.array(x['lengths'])-segments[:-1].sum(axis=0)
                    blocks=[]
                    for j in range(1,n):
                        rs=identifier(x,b,j/n,used);blocks.append(new_section(x,b,j/n,segments[j],rs,section_only));inserted.append(dict(interval=it['id'],river=r['river'],reach=r['reach'],station=rs,fraction=j/n,provenance='HEC_RAS_6.6_seccion_nativa_contrastada' if not section_only and levees.inherited_review(x,b) else 'string_model_5_geometry_4_roughness',spacing_m=float(segments[j,1])))
                    out+=g.length_header(x,segments[0])+x['block'].split('\n',1)[1]+''.join(blocks)
                except (ValueError,IndexError) as e:
                    blocked.append(dict(interval=it['id'],error=str(e)));out+=x['block']
            else:out+=x['block']
            if it:
                review=levees.inherited_review(x,r['xs'][i+1])
                ledger.append(dict(**it,selected=bool(selected),target_spacing_m=h,inserted=n-1,segments=n,mean_spacing_m=x['lengths'][1]/n,max_spacing_m=x['lengths'][1]/n,inherited_source_review=review['id'] if review else '',source_warning='Levees solo en originales. Revisar extensión lateral de flujo.' if section_only and n>1 else 'Levee/terreno heredado: bases conservadas, no validación física' if review else ''))
    require(not blocked,'No se generó el caso. Intervalos inseguros: '+str(blocked))
    out+=footer;validate(text,out,river);return out,ledger,inserted
def validate(base,new,river):
    p,rr,f=parse(base);q,ss,e=parse(new);require(p==q and f==e and len(rr)==len(ss),'Conectividad o metadatos alterados')
    for r,s in zip(rr,ss):
        require(r['header']==s['header'],'Cabecera Reach alterada');by={x['station']:x for x in s['xs']};pos={x['station']:i for i,x in enumerate(s['xs'])}
        require(len(by)==len(s['xs']),'RS duplicada')
        for x in r['xs']:require(x['station'] in by and g.canonical_body(x)==g.canonical_body(by[x['station']]),'Cuerpo base alterado: '+x['station'])
        for x,y in zip(r['xs'],r['xs'][1:]):
            lens=np.sum([z['lengths'] for z in s['xs'][pos[x['station']]:pos[y['station']]]],axis=0);require(np.allclose(lens,x['lengths'],atol=.00002,rtol=0),'Longitudes acumuladas alteradas')
        require(r['xs'][-1]['block']==s['xs'][-1]['block'],'Sección terminal alterada')
        if r['river']!=river:require([x['block'] for x in r['xs']]==[x['block'] for x in s['xs']],'Afluente alterado')
        for x in s['xs']:manning(x)
