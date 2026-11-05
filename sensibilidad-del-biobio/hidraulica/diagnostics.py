"""Native COM solver indicators; do not claim to export the full UI warning table."""
import math,re
from .common import require,save,sha,now
from .model import project,flows

def text_alerts(message):
    out=[]
    for line in message.splitlines():
        if re.search(r'\b(error|warning|failed|fatal|unable|not converg)',line,re.I) and not re.search(r'\b(no|0)\s+(errors?|warnings?)\b',line,re.I):
            out.append(dict(severity='error' if re.search(r'error|failed|fatal|unable',line,re.I) else 'warning',message=line))
    return out

def collect(c,prj):
    p=project(prj);names=flows(p['flow'].read_text(encoding='cp1252'))[0]
    c.Output_Initialize();n,v,descriptions=c.Output_Variables(None,None,None)
    wanted=['Std Stp Case','Headloss','Conv. Ratio','Num Trials','Crit Num']
    indices={name:list(v).index(name)+1 for name in wanted};rows=[];alerts=[];missing=[]
    nr,rivers=c.Output_GetRivers(None,None)
    for ir,river in enumerate(rivers,1):
        _,nreach,reaches=c.Output_GetReaches(ir,None,None)
        for jr,reach in enumerate(reaches,1):
            _,_,nn,stations,types=c.Output_GetNodes(ir,jr,None,None,None)
            for k,(station,typ) in enumerate(zip(stations,types),1):
                if typ.strip():continue
                for ip,profile in enumerate(names,1):
                    row=dict(river=river.strip(),reach=reach.strip(),station=station.strip(),profile=profile)
                    for name,idx in indices.items():
                        value=c.Output_NodeOutput(ir,jr,k,0,ip,idx);value=float(value[0] if isinstance(value,tuple) else value)
                        valid=math.isfinite(value) and abs(value)<1e30 and value!=-9999
                        row[name]=value if valid else None
                        if not valid:missing.append(dict(**{k:row[k] for k in ['river','reach','station','profile']},variable=name))
                    rows.append(row)
                    for condition,code,message in [
                        (row['Std Stp Case'] is not None and row['Std Stp Case']>=2,'solver_standard_step','Solución con mínimo error o recurso a profundidad crítica; revisar convergencia nativa.'),
                        (row['Headloss'] is not None and row['Headloss']>.3048,'energy_loss','Pérdida de energía >0.3048 m entre secciones; revisar espaciamiento.'),
                        (row['Conv. Ratio'] is not None and row['Conv. Ratio']>0 and not .7<=row['Conv. Ratio']<=1.4,'conveyance_ratio','Relación de conveyance fuera de 0.7–1.4.'),
                        (row['Crit Num'] is not None and row['Crit Num']>1,'multiple_critical_depths','Más de una profundidad crítica encontrada.')]:
                        if condition:alerts.append(dict(**row,code=code,severity='warning',message=message))
    report=dict(utc=now(),hdf_sha256=sha(p['hdf']),profiles=names,rows=rows,alerts=alerts,missing_indicators=missing,
                variable_descriptions={name:descriptions[indices[name]-1] for name in wanted},
                coverage='Indicadores nativos COM y mensajes de cálculo. No es una exportación completa de la tabla Errors/Warnings/Notes de la interfaz.',
                full_native_warning_table=False)
    save(p['prj'].parent/'diagnostico_nativo.json',report)
    return report
