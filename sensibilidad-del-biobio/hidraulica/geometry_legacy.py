"""Strict parser and string interpolation for the audited BIOBIO 1D geometry.
No terrain access. Original XS bodies and junction records are retained verbatim.
"""
from pathlib import Path
import re, math, hashlib
import numpy as np

def read(path):
    return Path(path).read_bytes().decode('cp1252').replace('\r\n','\n')

def write(path, text):
    Path(path).write_bytes(text.replace('\r\n','\n').replace('\n','\r\n').encode('cp1252'))

def field(text, key):
    m=re.search(r'^'+re.escape(key)+r'=(.*)$',text,re.M)
    if not m: raise ValueError(f'Campo ausente: {key}')
    return m.group(1).strip()

def pairs(block, key, width=8):
    lines=block.splitlines(); i=next(i for i,l in enumerate(lines) if l.startswith(key+'='))
    count=int(lines[i].split('=')[1].split(',')[0]); vals=[]
    for l in lines[i+1:]:
        if '=' in l or not l.strip(): break
        vals.extend(float(l[j:j+width]) for j in range(0,len(l),width) if l[j:j+width].strip())
        if len(vals)>=count*2: break
    if len(vals)!=count*2: raise ValueError(f'Formato no soportado {key}: {count}, {len(vals)}')
    return np.array(vals).reshape(-1,2)

def parse(text):
    reaches=[]
    starts=list(re.finditer(r'^River Reach=.*$',text,re.M))
    footer=re.search(r'^LCMann Time=',text,re.M)
    if not starts or not footer: raise ValueError('Geometria no soportada')
    for k,m in enumerate(starts):
        end=starts[k+1].start() if k+1<len(starts) else footer.start()
        part=text[m.start():end]; nodes=list(re.finditer(r'^Type RM Length L Ch R =.*$',part,re.M))
        river,reach=[s.strip() for s in m.group().split('=',1)[1].split(',')]
        xs=[]
        for i,n in enumerate(nodes):
            block=part[n.start():nodes[i+1].start() if i+1<len(nodes) else len(part)]
            values=[s.strip() for s in n.group().split('=',1)[1].split(',')]
            if values[0]!='1': raise ValueError('Se encontro estructura: se requiere tratamiento nativo')
            station=values[1]; xy=pairs(block,'#Sta/Elev'); lengths=list(map(float,values[2:5]))
            if not np.all(np.diff(xy[:,0])>0): raise ValueError(f'Estaciones no crecientes {river} {station}')
            banks=list(map(float,field(block,'Bank Sta').split(',')))
            xs.append(dict(river=river,reach=reach,station=station,rm=float(station.rstrip('*')),lengths=lengths,xy=xy,banks=banks,block=block))
        if any(xs[i]['rm']<=xs[i+1]['rm'] for i in range(len(xs)-1)): raise ValueError('Orden longitudinal invalido')
        reaches.append(dict(river=river,reach=reach,header=part[:nodes[0].start()],xs=xs))
    return text[:starts[0].start()],reaches,text[footer.start():]

def key(x): return (x['river'],x['reach'],x['station'])

def canonical_body(x): return x['block'].split('\n',1)[1].strip()

def anchors(x):
    xy=x['xy']; left,right=x['banks']; inside=xy[(xy[:,0]>=left)&(xy[:,0]<=right)]
    invert=inside[np.argmin(inside[:,1]),0]
    return np.array([xy[0,0],left,invert,right,xy[-1,0]])

def interpolate_xy(a,b,t):
    aa,bb=anchors(a),anchors(b); points=[]
    for i in range(4):
        da,db=aa[i+1]-aa[i],bb[i+1]-bb[i]
        if da==0 and db==0: continue
        if da<=0 or db<=0: raise ValueError('Cordones degenerados distintos: revisar interpolacion nativa')
        ua=(a['xy'][:,0]-aa[i])/da; ub=(b['xy'][:,0]-bb[i])/db
        u=np.unique(np.r_[0,1,ua[(ua>0)&(ua<1)],ub[(ub>0)&(ub<1)]])
        xa=aa[i]+u*da; xb=bb[i]+u*db
        z=(1-t)*np.interp(xa,a['xy'][:,0],a['xy'][:,1])+t*np.interp(xb,b['xy'][:,0],b['xy'][:,1])
        xx=(1-t)*xa+t*xb
        points.extend(zip(xx,z))
    p=np.array(points); p=p[np.r_[True,np.diff(p[:,0])>1e-7]]
    if len(p)>500: raise ValueError('Mas de 500 coordenadas interpoladas; no simplificar automaticamente')
    return p

def fixed_pairs(p,width=8,precision=3,perline=5):
    tokens=[]
    for x in np.asarray(p).ravel():
        s=f'{x:.{precision}f}'.rstrip('0').rstrip('.')
        if len(s)>width: raise ValueError('Coordenada excede ancho fijo')
        tokens.append(s.rjust(width))
    return '\n'.join(''.join(tokens[i:i+2*perline]) for i in range(0,len(tokens),2*perline))+'\n'

def length_header(x,lengths,station=None):
    return 'Type RM Length L Ch R = 1 ,'+(station or x['station'])+','+','.join(f'{v:.6f}' for v in lengths)+'\n'

def mann_block(x):
    return re.search(r'^#Mann=.*\n[^\n]*',x['block'],re.M).group()

def new_section(a,b,t,lengths):
    # Original Manning is piecewise constant on this project; reject gradients.
    if mann_block(a)!=mann_block(b): raise ValueError('Manning distinto entre extremos: politica explicita requerida')
    raw=interpolate_xy(a,b,t)
    target=(1-t)*np.array(a['banks'])+t*np.array(b['banks'])
    bank_indices=[int(np.argmin(abs(raw[:,0]-v))) for v in target]
    if any(abs(raw[i,0]-v)>1e-7 for i,v in zip(bank_indices,target)):
        raise ValueError('Falta un cordon de banco en el perfil interpolado')
    p=np.round(raw,3)
    # One quantization shared by both records, never independently rounded.
    banks=p[bank_indices,0].copy()
    # Merge only coordinates coinciding at file precision (1 mm), retain lowest z.
    ux=np.unique(p[:,0]); p=np.array([(x,p[p[:,0]==x,1].min()) for x in ux])
    # S8 station fields in HEC-RAS HDF: integer identifier plus interpolation mark.
    # Distances are always separate floating-point reach lengths, never RM differences.
    rs=f"{(1-t)*a['rm']+t*b['rm']:.0f}"+'*'
    cuta=pairs(a['block'],'XS GIS Cut Line',16); cutb=pairs(b['block'],'XS GIS Cut Line',16)
    if cuta.shape!=cutb.shape: raise ValueError('Cut lines incompatibles')
    cut=(1-t)*cuta+t*cutb
    # Levees at original XS are retained. No unobserved longitudinal levee is invented.
    # Such intervals require explicit visual review before campaign approval.
    ht=field(a['block'],'XS HTab Starting El and Incr').split(',')
    exp=field(a['block'],'Exp/Cntr')
    if exp!=field(b['block'],'Exp/Cntr'): raise ValueError('Exp/Cntr variable no soportado')
    start=float(p[:,1].min())+.15; step=float(ht[1]); count=max(20,math.ceil((p[:,1].max()-start)/step)+10)
    return (length_header(a,lengths,rs)+f'Node Name=INT {a["station"]}-{b["station"]}\n'
        +'XS GIS Cut Line='+str(len(cut))+'\n'+fixed_pairs(cut,16,3,2)
        +'#Sta/Elev= '+str(len(p))+'\n'+fixed_pairs(p)
        +mann_block(a)+'\n'+f'Bank Sta={banks[0]:.3f},{banks[1]:.3f}\n'
        +'XS Rating Curve= 0 ,0\n'+f'XS HTab Starting El and Incr={start:.3f},{step},{count}\n'
        +'XS HTab Horizontal Distribution= '+field(a['block'],'XS HTab Horizontal Distribution')+'\n'
        +'Exp/Cntr='+exp+'\n\n')

def build(text,limits):
    prefix,reaches,footer=parse(text); output=prefix; ledger=[]
    for reach in reaches:
        output+=reach['header']; xs=reach['xs']; limit=limits.get((reach['river'],reach['reach']))
        for i,a in enumerate(xs):
            n=1 if i==len(xs)-1 or limit is None else max(1,math.ceil(a['lengths'][1]/limit))
            if i<len(xs)-1 and a['lengths'][1]<=0: raise ValueError('Distancia hidraulica interna nula')
            segments=np.repeat(np.array(a['lengths'])[None,:]/n,n,axis=0)
            segments=np.round(segments,6); segments[-1]=np.array(a['lengths'])-segments[:-1].sum(axis=0)
            output+=a['block'] if n==1 else length_header(a,segments[0])+a['block'].split('\n',1)[1]
            if i==len(xs)-1: continue
            b=xs[i+1]
            ledger.append(dict(river=a['river'],reach=a['reach'],upstream=a['station'],downstream=b['station'],distance_channel=a['lengths'][1],rm_difference=a['rm']-b['rm'],max_spacing=limit,subintervals=n,spacing=a['lengths'][1]/n,levee_review=('Levee=' in a['block'] or 'Levee=' in b['block'])))
            for j in range(1,n): output+=new_section(a,b,j/n,segments[j])
    output+=footer
    validate(text,output,limits)
    return output,ledger

def validate(original,candidate,limits):
    p,rr,f=parse(original); q,ss,g=parse(candidate)
    if p!=q or f!=g or len(rr)!=len(ss): raise ValueError('Conectividad o metadatos cambiaron')
    rows=[]
    for r,s in zip(rr,ss):
        if r['header']!=s['header']: raise ValueError('Cabecera del tramo cambio')
        orig={key(x):x for x in r['xs']}; new={key(x):x for x in s['xs']}
        if len(new)!=len(s['xs']): raise ValueError('Secciones duplicadas')
        for k,x in orig.items():
            if k not in new or canonical_body(x)!=canonical_body(new[k]): raise ValueError(f'Seccion original alterada {k}')
        pos={key(x):i for i,x in enumerate(s['xs'])}
        for i,a in enumerate(r['xs'][:-1]):
            b=r['xs'][i+1]; interval=s['xs'][pos[key(a)]:pos[key(b)]]
            if not np.allclose(np.sum([x['lengths'] for x in interval],axis=0),a['lengths'],rtol=0,atol=2e-5): raise ValueError('Distancias no conservadas')
            lim=limits.get((a['river'],a['reach']))
            if lim and max(x['lengths'][1] for x in interval)>lim+1e-5: raise ValueError('Espaciamiento excedido')
        if s['xs'][-1]['lengths']!=r['xs'][-1]['lengths']: raise ValueError('Distancia terminal modificada')
        for x in s['xs']:
            if not x['banks'][0]<x['banks'][1]: raise ValueError('Bancos no ordenados '+str(key(x)))
            for bank in x['banks']:
                if not np.any(x['xy'][:,0]==bank):
                    raise ValueError('Bank Sta no existe exactamente en Sta/Elev '+str(key(x))+': '+str(bank))
            if not np.all(np.isfinite(x['lengths'])) or min(x['lengths'])<0: raise ValueError('Distancias invalidas')
            if not np.all(np.isfinite(x['xy'])) or not np.all(np.diff(x['xy'][:,0])>0): raise ValueError('Perfil invalido')
        rows.append(dict(river=r['river'],reach=r['reach'],original=len(orig),total=len(new)))
    return rows
