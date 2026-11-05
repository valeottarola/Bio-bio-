from pathlib import Path
from itertools import combinations
import numpy as np,pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from .common import *
from .model import project,reference_project
from .results import extract,PRIMARY,VARIABLES
from .campaign import load_config,completed,create_config,config_path
VARS=PRIMARY+[x for x in VARIABLES if x not in PRIMARY]
def get_case(label,out,model,folder):
    if label=='BASE':prj=reference_project(folder,model)
    else:
        case=out/'casos'/safe(label);meta=readjson(case/'caso.json');attempt=completed(case,meta,model['inventory']['sections']);require(attempt is not None,'Caso pendiente: '+label);prj=attempt/meta['project']
    df,info=extract(prj,model['inventory']['sections']);return df,info
def build_panel(datasets,model):
    # Frozen union of spatial controls for every pair/profile in this analysis.
    frames=[d[d.river==model['river']][['river','reach','station','chainage_m','base']].drop_duplicates() for d in datasets.values()]
    joined=pd.concat(frames);joined['chainage_m']=joined.chainage_m.round(3)
    panel=joined.drop_duplicates(['river','reach','station','chainage_m']).sort_values(['river','reach','chainage_m']).reset_index(drop=True)
    panel['control']=['P'+str(i+1).zfill(6) for i in range(len(panel))]
    base=model['inventory']['sections'];intervals=model['inventory']['intervals'];bounds=[]
    for it in intervals:
        x=next(x for x in base if (x['river'],x['reach'],x['station'])==(it['river'],it['reach'],it['upstream']))
        bounds.append(dict(**it,start=x['chainage_m'],end=x['chainage_m']+it['length_m']))
    panel['interval_ids']=[';'.join(it['id'] for it in bounds if it['reach']==r.reach and it['start']-.003<=r.chainage_m<=it['end']+.003) for r in panel.itertuples()]
    return panel
def project_panel(df,panel,sections):
    rows=[]
    for p in panel.itertuples():
        r=df[(df.river==p.river)&(df.reach==p.reach)].sort_values('chainage_m');row=dict(control=p.control,method='no_information',reason='outside_coverage')
        exact=r[(abs(r.chainage_m-p.chainage_m)<=.003)&(r.station.map(lambda x:float(str(x).rstrip('*')))==float(str(p.station).rstrip('*')))]
        if len(exact)==1:row.update(exact.iloc[0][VARS].to_dict());row.update(method='direct_HDF',reason='')
        else:
            bases=[x['chainage_m'] for x in sections if x['river']==p.river and x['reach']==p.reach];lo=[x for x in bases if x<=p.chainage_m+.003];hi=[x for x in bases if x>=p.chainage_m-.003]
            if lo and hi:
                z=r[(r.chainage_m>=max(lo)-.003)&(r.chainage_m<=min(hi)+.003)];left=z[z.chainage_m<p.chainage_m];right=z[z.chainage_m>p.chainage_m]
                if len(left) and len(right):
                    a=left.iloc[-1];b=right.iloc[0]
                    if (a.froude-1)*(b.froude-1)<=0 or min(abs(a.froude-1),abs(b.froude-1))<.05:row['reason']='critical_regime_proximity'
                    else:
                        t=(p.chainage_m-a.chainage_m)/(b.chainage_m-a.chainage_m);row.update({v:a[v]+t*(b[v]-a[v]) for v in VARS});row.update(method='spatial_estimate',reason='');row['upstream']=a.station;row['downstream']=b.station
        rows.append(row)
    return pd.DataFrame(rows).reindex(columns=['control','method','reason','upstream','downstream']+VARS)
def metric_rows(d,pair,profile,tol):
    rows=[]
    for group,z in [('all_controls',d),('direct_both',d[d.direct_both]),('base_sections',d[d.base])]:
        for v in VARS:
            y=z['delta_'+v].dropna();idx=y.abs().idxmax() if len(y) else None
            rows.append(dict(pair=pair,profile=profile,group=group,variable=v,n_controls=len(z),n_evaluated=len(y),n_missing=len(z)-len(y),max_abs=y.abs().max(),MAE=y.abs().mean(),RMSE=np.sqrt((y*y).mean()),P95=y.abs().quantile(.95),bias=y.mean(),exceedances=int((y.abs()>tol[v]).sum()) if v in tol else None,max_reach=d.loc[idx,'reach'] if idx is not None else '',max_station=d.loc[idx,'station'] if idx is not None else ''))
    return rows
def analyze(name,reference='BASE',candidates=None,mode='sensitivity'):
    c,out,m,folder=load_config(name);candidates=candidates or [r['name'] for r in c['refinements']]
    labels=list(dict.fromkeys(([reference]+candidates) if mode!='convergence' else candidates));require(len(labels)>=2,'Seleccione al menos dos geometrías distintas')
    data={};hashes={};contexts={}
    for label in labels:
        d,info=get_case(label,out,m,folder);require(set(c['profiles'])<=set(d.profile),'Perfiles incompatibles');data[label]=d[d.profile.isin(c['profiles'])];hashes[label]=info['hdf_sha256'];contexts[label]=info['profiles']
    panel=build_panel(data,m);pairs = list(combinations(labels,2)) if mode=='all_pairs' else (list(zip(labels,labels[1:])) if mode=='convergence' else [(reference,x) for x in candidates if x!=reference])
    require(pairs,'No hay pares de comparación');runid=datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f');dest=out/'analisis'/runid;dest.mkdir(parents=True)
    mismatch=any(contexts[a]!=contexts[b] for a,b in pairs)
    panel.to_csv(dest/'panel_fijo.csv',index=False,encoding='utf-8-sig');save(dest/'contrato.json',dict(config=c,hashes=hashes,profile_contexts=contexts,profile_context_mismatch=mismatch,mode=mode,pairs=pairs,controls=len(panel),created_utc=now(),native_warnings_reviewed=False))
    metrics=[];all_comparisons=[];intervalrows=[]
    for profile in c['profiles']:
        projected={label:project_panel(d[d.profile==profile],panel,m['inventory']['sections']) for label,d in data.items()}
        for ref,cand in pairs:
            pair=cand+'-vs-'+ref;x=projected[ref].merge(projected[cand],on='control',suffixes=('_ref','_cand'),validate='one_to_one');x=panel.merge(x,on='control',validate='one_to_one');x['pair']=pair;x['profile']=profile
            x['direct_both']=(x.method_ref=='direct_HDF')&(x.method_cand=='direct_HDF');x['evaluated']=(x.method_ref!='no_information')&(x.method_cand!='no_information')
            for v in VARS:x['delta_'+v]=x[v+'_cand']-x[v+'_ref']
            x['exceeds']=np.logical_or.reduce([x['delta_'+v].abs()>c['tolerances'][v] for v in PRIMARY])&x.evaluated
            x['regime_change']=((x.froude_ref-1)*(x.froude_cand-1)<0)&x.evaluated;metrics+=metric_rows(x,pair,profile,c['tolerances']);all_comparisons.append(x)
            for it in m['inventory']['intervals']:
                z=x[x.interval_ids.str.split(';').map(lambda ids:it['id'] in ids)];ratio=max((z['delta_'+v].abs()/c['tolerances'][v]).max() for v in PRIMARY)
                intervalrows.append(dict(pair=pair,profile=profile,interval=it['id'],reach=it['reach'],upstream=it['upstream'],downstream=it['downstream'],max_ratio=ratio,n_controls=len(z),missing=int((~z.evaluated).sum()),direct_exceed=int((z.exceeds&z.direct_both).sum()),estimated_alerts=int((z.exceeds&~z.direct_both).sum()),decision='refinar_o_revisar' if z.exceeds.any() or (~z.evaluated).any() else 'acuerdo_provisional_no_convergencia'))
    M=pd.DataFrame(metrics);D=pd.concat(all_comparisons,ignore_index=True);I=pd.DataFrame(intervalrows)
    for filename,df in [('metricas',M),('comparaciones',D),('intervalos_sensibles',I)]:df.to_csv(dest/(filename+'.csv'),index=False,encoding='utf-8-sig')
    counts=[dict(case=k,river_sections=int((d[(d.profile==c['profiles'][0])].river==m['river']).sum()),total_sections=len(d[d.profile==c['profiles'][0]])) for k,d in data.items()];pd.DataFrame(counts).to_csv(dest/'conteos.csv',index=False,encoding='utf-8-sig')
    nested=[]
    for ref,cand in pairs:
        def positions(d):return {(x.river,x.reach,round(x.chainage_m,3)) for x in d[d.profile==c['profiles'][0]].itertuples()}
        nested.append(dict(reference=ref,candidate=cand,nested=positions(data[ref])<=positions(data[cand])))
    save(dest/'anidamiento.json',nested)
    for profile in c['profiles']:
        fig,axes=plt.subplots(2,2,figsize=(12,8),constrained_layout=True)
        for ax,v in zip(axes.flat,PRIMARY):
            for pair in D.pair.unique():
                z=D[(D.profile==profile)&(D.pair==pair)];ax.plot(range(len(z)),z['delta_'+v].abs(),'.',ms=2,label=pair)
            ax.axhline(c['tolerances'][v],color='red',ls=':');ax.set_title(v);ax.set_xlabel('Control fijo (Reach / distancia en CSV)');ax.grid(alpha=.2)
        axes.flat[0].legend(fontsize=7);fig.suptitle(profile+' — diferencias directas y estimadas (faltantes en CSV)');fig.savefig(dest/('errores_'+str(c['profiles'].index(profile)+1)+'.png'),dpi=130);plt.close(fig)
    profiles_long=pd.concat([d.assign(caso=label) for label,d in data.items()],ignore_index=True)
    profiles_long.to_csv(dest/'longitudinal_por_reach.csv',index=False,encoding='utf-8-sig')
    spacing_tables=[]
    for label in labels:
        ledger=out/'casos'/label/'intervalos.csv'
        if ledger.exists():
            spacing_tables.append(pd.read_csv(ledger).assign(caso=label))
    if spacing_tables:
        pd.concat(spacing_tables,ignore_index=True).to_csv(dest/'espaciamientos_reales.csv',index=False,encoding='utf-8-sig')
    for reach,group in profiles_long[profiles_long.river==m['river']].groupby('reach'):
        fig,axes=plt.subplots(len(c['profiles']),4,figsize=(15,12),squeeze=False,constrained_layout=True)
        for row,profile in enumerate(c['profiles']):
            for col,var in enumerate(PRIMARY):
                ax=axes[row,col]
                for label,series in group[group.profile==profile].groupby('caso'):
                    series=series.sort_values('chainage_m')
                    ax.plot(series.chainage_m/1000,series[var],label=label,lw=1)
                ax.set_title(profile+' '+var,fontsize=8);ax.grid(alpha=.2)
        axes[0,0].legend(fontsize=5)
        fig.supxlabel('Distancia hidráulica dentro del Reach (km)')
        fig.savefig(dest/('longitudinal_'+reach+'.png'),dpi=120);plt.close(fig)
    if mode=='convergence':
        interval_data=pd.DataFrame(intervalrows)
        records=[]
        for (iid,reach),group in interval_data.groupby(['interval','reach'],sort=False):
            steps=list(group.pair.unique())
            last=group[group.pair==steps[-1]]
            history=[group.loc[group.pair==step,'max_ratio'].max() for step in steps]
            recent=group[group.pair.isin(steps[-2:])]
            complete=len(last)==6 and last.n_controls.gt(0).all() and recent.missing.sum()==0
            stable=len(steps)>=2 and complete and max(history[-2:])<=1
            state=('acuerdo_provisional' if stable else 'controles_insuficientes' if not complete else
                   'sensibilidad_decreciente' if len(history)>=2 and history[-1]<history[-2] else
                   'sensibilidad_persistente_o_evidencia_limitada')
            chosen=labels[-1]
            ledger_path=out/'casos'/chosen/'intervalos.csv'
            ledger=pd.read_csv(ledger_path) if ledger_path.exists() else pd.DataFrame()
            row=ledger[ledger.id==iid] if 'id' in ledger else pd.DataFrame()
            spacing=float(row.iloc[0].max_spacing_m) if not row.empty else np.nan
            count=int(row.iloc[0].inserted) if not row.empty else None
            records.append(dict(intervalo=iid,reach=reach,estado=state,comparaciones=len(steps),
                                ratio_ultimo=history[-1],caso=chosen,espaciamiento_evaluado_m=spacing,
                                secciones_insertadas=count,espaciamiento_provisional_m=spacing if stable else np.nan))
        local=pd.DataFrame(records)
        local.to_csv(dest/'estabilizacion_por_intervalo.csv',index=False,encoding='utf-8-sig')
        local.groupby(['reach','estado']).agg(intervalos=('intervalo','size'),
            secciones_insertadas=('secciones_insertadas','sum'),
            espaciamiento_provisional_max_m=('espaciamiento_provisional_m','max')).to_csv(
                dest/'estabilizacion_por_reach.csv',encoding='utf-8-sig')
    export_report(dest);return dest
def export_report(dest):
    """Local Python export: no cloud, Codex runtime, or API required."""
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill
    from openpyxl.utils import get_column_letter
    dest=Path(dest);M=pd.read_csv(dest/'metricas.csv');I=pd.read_csv(dest/'intervalos_sensibles.csv');D=pd.read_csv(dest/'comparaciones.csv');contract=readjson(dest/'contrato.json');wb=Workbook();wb.remove(wb.active)
    for title,df in [('Metricas',M),('Intervalos',I),('Comparaciones',D),('Conteos',pd.read_csv(dest/'conteos.csv'))]:
        require(len(df)<1048575,'Tabla excede capacidad Excel; use CSV');ws=wb.create_sheet(title);ws.append(list(df.columns))
        for row in df.itertuples(index=False,name=None):
            ws.append([None if pd.isna(v) else ("'"+v if isinstance(v,str) and v[:1] in '=+-@' else v) for v in row])
        for cell in ws[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='17476A')
        ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
        for j,col in enumerate(df.columns,1):ws.column_dimensions[get_column_letter(j)].width=min(36,max(14,len(col)+2))
    wb.save(dest/'resultados.xlsx')
    direct=D[D.direct_both&D.exceeds];estimated=D[~D.direct_both&D.exceeds];missing=D[~D.evaluated]
    md=f'# Análisis hidráulico local\n\nFecha UTC: {now()}\n\nCampaña: {contract["config"]["name"]}. Modo: {contract["mode"]}. Panel fijo: {contract["controls"]} controles (unión de posiciones de todos los casos seleccionados).\n\nIncumplimientos directos (registros perfil/par): {len(direct)}. Alertas estimadas: {len(estimated)}. Controles sin información (perfil/par): {len(missing)}.\n\n'
    if contract.get('profile_context_mismatch'):
        md+='**Contexto de perfiles distinto:** estos HDF se calcularon con listas u órdenes de perfiles diferentes. Sus diferencias no aíslan exclusivamente la geometría; para convergencia controlada use casos calculados con la misma selección y orden. Ver profile_contexts en contrato.json.\n\n'
    md+='## Interpretación\n\nDiferencia = candidato − referencia. WSE/profundidad en m, velocidad en m/s; Froude total derivado con g=9,80665. Los umbrales figuran en contrato.json. Área activa, área incluyendo inefectivas, radio y pendiente son complementarios sin tolerancias inventadas.\n\nSe evalúan todos los perfiles seleccionados y no se eliminan controles faltantes. Interpolación solo dentro del mismo Reach e intervalo base; se bloquea en cambios de Fr−1 y proximidad |Fr−1|<0,05 en extremos. Las estimaciones no demuestran incumplimientos hidráulicos directos ni suficiencia espacial. No se interpolan sobre confluencias.\n\nConvergencia: se comparan pasos sucesivos en el MISMO panel; consultar máximos, MAE, RMSE, P95 y ubicaciones por perfil. anidamiento.json identifica mallas anidadas. No se infiere orden formal ni convergencia por cantidad de secciones. Un buen acuerdo provisional no demuestra un mínimo ni validación física. Los avisos nativos por sección requieren revisión en HEC-RAS; los mensajes de cálculo están en cada intento.\n\n'
    md+='Los intentos calculados con la actualización incluyen diagnostico_nativo.json, indicadores_nativos.csv y avisos_numericos.csv. Los criterios sobre indicadores COM no sustituyen la tabla completa de avisos UI. Los pilotos del modelo MNMFNuevo antiguo sin Levees no validan MNMFNuevo_LEVEES_02NOV.\n\n'
    md+='## Resultados por perfil y par\n\n|Par|Perfil|Variable|Máximo absoluto|RMSE|P95|Directos fuera|\n|---|---|---|---:|---:|---:|---:|\n'
    for r in M[(M.group=='direct_both')&M.variable.isin(PRIMARY)].itertuples():md+=f'|{r.pair}|{r.profile}|{r.variable}|{r.max_abs:.6g}|{r.RMSE:.6g}|{r.P95:.6g}|{r.exceedances:g}|\n'
    md+='\nLos CSV conservan métodos, diferencias y ubicaciones. Los intervalos base comparten extremos, que pueden aparecer en dos resúmenes locales; el conteo global por control es único. La procedencia de las secciones base está en modelo.json: base no significa levantamiento original. La incertidumbre geométrica física y la discretización son problemas distintos.\n'
    (dest/'INFORME.md').write_text(md,encoding='utf-8')
    return dest
def propose(name,analysis_dir,new_name):
    c,out,m,folder=load_config(name);contract=readjson(Path(analysis_dir)/'contrato.json');require(contract['config']==c,'Análisis de otra configuración');I=pd.read_csv(Path(analysis_dir)/'intervalos_sensibles.csv')
    grouped=I.groupby('interval').agg(max_ratio=('max_ratio','max'),missing=('missing','sum'),exceed=('direct_exceed','sum'))
    finest=min(c['refinements'],key=lambda x:x['spacing_m']);h=finest['spacing_m'];overrides={};decisions=[]
    for iid,r in grouped.iterrows():
        if r['missing']>0 or r.max_ratio>1:target=h/2;reason='refinamiento_exploratorio'
        elif r.max_ratio<.2:target=h*2;reason='reduccion_exploratoria_no_aprobada'
        else:target=h;reason='conservar'
        overrides[iid]=target;decisions.append(dict(interval=iid,target_m=target,reason=reason))
    config=create_config(new_name,c['model'],[h],c['profiles'],output=None,overrides=overrides);config['refinements'][0]['name']='LOCAL_PROPUESTO';config['optimization_basis']=str(Path(analysis_dir).resolve());config['warning']='Propuesta exploratoria, no mínimo ni geometría aprobada. Revise avisos antes de ejecutar.';save(config_path(new_name),config)
    save(config_path(new_name).with_suffix('.propuesta.json'),decisions);return config
