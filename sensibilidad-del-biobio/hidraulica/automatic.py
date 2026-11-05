"""One-selection workflow. Settings are permanent; unsafe work fails explicitly."""
import datetime,time,shutil,traceback,itertools,os,html
from pathlib import Path
import pandas as pd
from .common import ROOT,readjson,save,sha,digest,require,now,ValidationError
from . import model,campaign,geometry,levees,analysis
from .results import extract,PRIMARY

ACTIONS={'1':'verify','2':'generate','3':'run','4':'analyze','5':'optimize','6':'report','7':'all'}

class Workflow:
    def __init__(self,settings=None):
        self.settings_path=Path(settings) if settings else ROOT/'CONFIGURACION_AUTOMATICA.json'
        self.s=readjson(self.settings_path);s=self.s
        require(s['model']=='MNMFNuevo_LEVEES_02NOV','El modo principal requiere MNMFNuevo_LEVEES_02NOV')
        require(s['river']=='Biobio' and s['intervals']=='all','El modo principal abarca los 44 intervalos del Biobío')
        require(s['profiles']==['PF '+str(i) for i in range(1,7)],'Se requieren los seis perfiles oficiales, en orden')
        require(s['spacings_m']==[2000,1000,500,250],'Campaña principal requiere R2000/R1000/R500/R250')
        require(s['tolerances']==dict(wse_m=.05,velocity_ms=.05,hydraulic_depth_m=.05,froude=.05),'Tolerancias oficiales distintas')
        limits=s['limits'];require(all(isinstance(limits[k],int) and limits[k]>0 for k in ['max_extra_cases','max_total_sections','case_timeout_seconds','optimization_seconds']),'Límites inválidos')
        require(0<limits['minimum_spacing_m']<=250,'Espaciamiento mínimo inválido')
        self.run_id=datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        self.dest=ROOT/'informes_automaticos'/self.run_id;self.dest.mkdir(parents=True)
        self.log=[];self.errors=[];self.generated=[];self.analyses=[];self.audit=[];self.optimizer={};self.verified=False
        save(self.dest/'configuracion_usada.json',s)
    def event(self,stage,status,**detail):
        row=dict(utc=now(),stage=stage,status=status,**detail);self.log.append(row);save(self.dest/'etapas.json',self.log);print(stage+': '+status,detail.get('message',''),flush=True)
    def stage(self,stage,fn):
        self.event(stage,'iniciado')
        try:
            result=fn();self.event(stage,'terminado');return result
        except Exception as e:
            item=dict(stage=stage,error=str(e),traceback=traceback.format_exc());self.errors.append(item);save(self.dest/'errores.json',self.errors);self.event(stage,'bloqueado',message=str(e));return None
    def verify(self):
        s=self.s;source=Path(s['source_project']);p=model.project(source)
        require(sha(p['geom'])==s['geometry_sha256'],'La geometría de origen cambió: no corresponde al hash definitivo configurado')
        if not (ROOT/'modelos'/s['model']/'modelo.json').exists():model.import_model(source,s['model'],s['river'])
        folder,m=model.load_model(s['model']);v=model.verify(folder/m['project'],s['river']);current=model.verify(source,s['river'])
        require(current['hashes']==v['hashes'],'Proyecto/plan/caudales originales cambiaron respecto de la copia oficial; registrar versión explícita en modo avanzado')
        require((v['base_count'],v['tributary_count'],v['reach_count'],len(v['intervals']),sum(x['levee_record'] for x in v['sections']))==(50,10,6,44,31),'Inventario definitivo no coincide: se exigen 50+10 secciones, seis Reach,44 intervalos,31 Levees')
        require(v['profiles']==s['profiles'],'Perfiles de fuente distintos de los configurados')
        model.set_official(s['model']);self.folder=folder;self.m=m;self.v=v
        hdf_problem=None
        try:
            d,info=extract(model.reference_project(folder,m),v['sections']);require(len(d)==360,'HDF base incompleto')
            supplied,sinfo=extract(source,v['sections'])
            require(info['hdf_sha256']==sinfo['hdf_sha256'],'HDF de fuente y referencia no son idénticos; verificar mediante recálculo independiente')
        except (ValueError,OSError,KeyError) as e:
            hdf_problem=str(e);self.event('verify/HDF','recalculo_independiente_requerido',message=hdf_problem)
            baseline='AUTO_BASE_'+digest(v['hashes'])[:12]
            if not campaign.config_path(baseline).exists():campaign.create_config(baseline,s['model'],[100000],s['profiles'])
            campaign.generate(baseline);campaign.run(baseline,timeout=s['limits']['case_timeout_seconds'])
            _,bo,_,_=campaign.load_config(baseline);case=bo/'casos/R100000';meta=readjson(case/'caso.json');attempt=campaign.completed(case,meta,v['sections']);d,info=extract(attempt/m['project'],v['sections'])
            save(ROOT/'referencias_base'/(s['model']+'.json'),dict(project_relative=str((attempt/m['project']).relative_to(ROOT)),hdf_sha256=info['hdf_sha256'],reason=hdf_problem,utc=now()))
            sinfo=dict(validation_error=hdf_problem,original_preserved=True)
        self.verified=True
        save(self.dest/'verificacion_modelo.json',dict(inventory=v,hdf=info,source_hdf=sinfo,plan_version=model.field(geometry.read(p['plan']),'Program Version'),runtime_required='6.6'))
        print(f"{s['model']}: 50 secciones del Biobío, 10 de afluentes, 31 registros de Levees, 44 intervalos y {len(d)} registros HDF en seis perfiles.")
        return v
    def ensure(self):
        if not self.verified:self.verify()
    def config(self):
        self.ensure();s=self.s
        if not campaign.config_path(s['campaign']).exists():campaign.create_config(s['campaign'],s['model'],s['spacings_m'],s['profiles'],section_only=True)
        c,out,m,f=campaign.load_config(s['campaign'])
        require(c['model']==s['model'] and c['profiles']==s['profiles'] and c['tolerances']==s['tolerances'],'Configuración principal incompatible')
        require([x['spacing_m'] for x in c['refinements']]==s['spacings_m'] and all(not x.get('intervals') and not x.get('reaches') and not x.get('overrides') for x in c['refinements']),'La campaña principal debe incluir todos los intervalos sin exclusiones')
        return c,out,m,f
    def generate(self):
        c,out,m,f=self.config();text=geometry.read(model.project(f/m['project'])['geom']);blocked=[]
        for spec in c['refinements']:
            rows=levees.audit(text,m['river'],spec['spacing_m'],section_only=True)
            for row in rows:row['case']=spec['name']
            self.audit+=rows
            bad=[x for x in rows if x['status']=='bloqueado']
            if bad:
                save(out/('BLOQUEADO_'+spec['name']+'.json'),dict(utc=now(),intervals=bad,complete=False));blocked.append(spec['name'])
                self.event('generate/'+spec['name'],'bloqueado',message=f'{len(bad)} intervalos incompatibles; no se genera caso parcial')
                continue
            n=60+sum(x.get('inserted',0) for x in rows)
            if n>self.s['limits']['max_total_sections']:
                blocked.append(spec['name']);self.event('generate/'+spec['name'],'bloqueado',message='Supera límite de secciones');continue
            try:
                campaign.generate(c['name'],[spec['name']])
                self.generated.append(spec['name'])
            except Exception as exc:
                blocked.append(spec['name'])
                self.event('generate/'+spec['name'],'bloqueado',message=str(exc))
        save(self.dest/'auditoria_44_intervalos.json',self.audit);save(out/'auditoria_ultimo.json',dict(settings_hash=digest(self.s),rows=self.audit));pd.DataFrame(self.audit).to_csv(self.dest/'auditoria_44_intervalos.csv',index=False,encoding='utf-8-sig')
        require(not blocked,'Generación incompleta: '+', '.join(blocked)+'. Ver auditoría exacta; no se omiten intervalos.')
    def run(self):
        c,out,m,f=self.config();pending=[];failed=[]
        for spec in c['refinements']:
            case=out/'casos'/spec['name']
            if not (case/'caso.json').exists():pending.append(spec['name']);continue
            try:campaign.run(c['name'],[spec['name']],self.s['limits']['case_timeout_seconds'])
            except Exception as e:failed.append(spec['name']+': '+str(e))
        require(not pending and not failed,'Casos no generados: '+', '.join(pending)+'; errores: '+' | '.join(failed)+'. Se conserva estado incompleto; consulte opción 2/informe.')
    def core_status(self):
        c,out,m,f=self.config();return campaign.status(c['name'])
    def available(self):
        self.ensure();valid=[];excluded=[]
        for p in sorted((ROOT/'campanas').glob('*/campana.json')):
            try:
                record=readjson(p)
                if record.get('read_only_ensemble'):continue
                contract=record['contract'];cc=contract['config']
                current=self.s['campaign']
                if cc['name'] not in (current,'CANDIDATO_349') and not cc['name'].startswith(current+'_LOCAL_'):
                    excluded.append(dict(campaign=cc['name'],reason='Campaña histórica conservada'))
                    continue
                if contract['source_hashes']!=self.v['hashes']:continue
                if cc['profiles']!=self.s['profiles']:
                    excluded.append(dict(campaign=cc['name'],reason='No calculada con los seis perfiles oficiales y su mismo orden'));continue
                for spec in cc['refinements']:
                    case=p.parent/'casos'/spec['name']
                    if not (case/'caso.json').exists():continue
                    meta=readjson(case/'caso.json');attempt=campaign.completed(case,meta,self.v['sections'])
                    if attempt is not None:valid.append(dict(label=(cc['name']+'__'+spec['name'])[:58]+'_'+digest(str(case))[:7],case=case,meta=meta,attempt=attempt,campaign=cc['name'],spacing=spec['spacing_m']))
            except Exception as e:excluded.append(dict(path=str(p),reason=str(e)))
        save(self.dest/'escenarios_excluidos.json',excluded)
        return valid
    def ensemble(self):
        cases=self.available();require(cases,'No hay casos calculados verificables con los seis perfiles; no se fabrica análisis')
        # Immutable analysis-only collection; source cases and receipts stay unchanged.
        tag='AUTO_LECTURA_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f');out=ROOT/'campanas'/tag;out.mkdir(parents=True)
        c=dict(schema=1,name=tag,model=self.s['model'],profiles=self.s['profiles'],refinements=[],tolerances=self.s['tolerances'],output_dir='campanas/'+tag)
        for item in cases:
            shutil.copytree(item['case'],out/'casos'/item['label'])
            c['refinements'].append(dict(name=item['label'],spacing_m=item['spacing'],intervals=[],reaches=[],overrides={}))
        save(campaign.config_path(tag),c);save(out/'campana.json',dict(read_only_ensemble=True,contract=dict(config=c,source_hashes=self.v['hashes']),created_utc=now()))
        save(self.dest/'escenarios_disponibles.json',[{k:str(v) if isinstance(v,Path) else v for k,v in x.items() if k!='meta'} for x in cases]);return tag,cases
    def analyze(self,all_pairs=False):
        tag,cases=self.ensemble();labels=['BASE']+[x['label'] for x in cases]
        active={x['meta']['name']:x['label'] for x in cases if x['campaign']==self.s['campaign']}
        if not all_pairs:
            order=['R2000','R1000','R500','R250']
            for a,b in zip(order,order[1:]):
                if a in active and b in active:
                    self.analyses.append(str(analysis.analyze(tag,candidates=[active[a],active[b]],mode='convergence')))
        dest=analysis.analyze(tag,'BASE',labels[1:],mode='all_pairs')
        self.analyses.append(str(dest))
        save(self.dest/'analisis_generados.json',self.analyses);return self.analyses
    def optimize(self):
        self.ensure()
        states=self.core_status()
        c,_,_,_=self.config()
        valid={x['case'] for x in states if x['state']=='completado_verificado'}
        labels=[x['name'] for x in c['refinements']]
        pairs=[(a,b) for a,b in zip(labels,labels[1:]) if a in valid and b in valid]
        require(pairs,'Faltan dos refinamientos sucesivos verificados para evaluar estabilización')
        if len(valid)<len(labels):
            for pair in pairs:
                dest=analysis.analyze(c['name'],candidates=list(pair),mode='convergence')
                self.analyses.append(str(dest))
            save(self.dest/'analisis_generados.json',self.analyses)
            return
        c,out,m,f=self.config();budget_path=out/'optimizacion_presupuesto.json';budget=readjson(budget_path) if budget_path.exists() else dict(cases=[],settings_hash=digest(self.s['limits']))
        require(budget['settings_hash']==digest(self.s['limits']),'Límites cambiados en optimización existente; crear campaña nueva explícitamente')
        started=time.monotonic();labels=[x['name'] for x in c['refinements']];latest=c['name'];space=250
        self.optimizer=dict(status='evaluando',recommendation=None)
        # Core convergence is always assessed on a single frozen panel.
        dest=analysis.analyze(c['name'],candidates=labels,mode='convergence');self.analyses.append(str(dest))
        for extra in budget['cases']:
            # Resume the exact approved generated experiments; never duplicate a valid case.
            campaign.generate(extra['campaign'])
            campaign.run(extra['campaign'],timeout=self.s['limits']['case_timeout_seconds'])
        if budget['cases']:
            tag,available=self.ensemble();ordered=[]
            for cn,case_name in [(c['name'],x) for x in labels]+[(b['campaign'],'R250') for b in budget['cases']]:ordered.append(next(a['label'] for a in available if a['campaign']==cn and a['meta']['name']==case_name))
            dest=analysis.analyze(tag,candidates=ordered,mode='convergence');self.analyses.append(str(dest));latest=budget['cases'][-1]['campaign'];space=budget['cases'][-1]['target_spacing_m']
        while True:
            d=pd.read_csv(dest/'comparaciones.csv');pairs=list(d.pair.unique());tail=d[d.pair.isin(pairs[-2:])]
            enough=len(pairs)>=2 and set(tail.profile)==set(self.s['profiles'])
            stable=enough and bool(tail.evaluated.all()) and not bool(tail.exceeds.any()) and not bool(tail.regime_change.any())
            diag=[]
            for row in self.available():
                if row['campaign'] in [c['name']]+[x['campaign'] for x in budget['cases']]:
                    dp=row['attempt']/'diagnostico_nativo.json'
                    if not dp.exists():diag.append('diagnostico_ausente')
                    else:
                        native=readjson(dp)
                        if any(x['code']=='solver_standard_step' for x in native['alerts']):diag.append('recurso_solver')
            self.optimizer.update(stable_last_two=stable,solver_flags=diag,budget=budget,analysis=str(dest))
            if stable:
                self.optimizer['status']='estabilizacion_numerica_observada' if not diag else 'acuerdo_con_avisos_solver'
                if not diag:
                    lc,lo,_,_=campaign.load_config(latest);selected=lc['refinements'][-1]['name'];cp=lo/'casos'/selected;cm=readjson(cp/'caso.json')
                    shutil.copy2(cp/'intervalos.csv',self.dest/'espaciamientos_recomendados.csv')
                    self.optimizer['recommendation']=dict(campaign=latest,case=selected,finest_target_spacing_m=space,river_sections=cm['river_count'],total_sections=cm['geometry_count'],interpolated_sections=cm['inserted_count'],interval_spacings='espaciamientos_recomendados.csv',scope='Resolución candidata entre las probadas; no mínimo global ni validación de geometría física. Tabla nativa completa requiere revisión.')
                break
            if len(budget['cases'])>=self.s['limits']['max_extra_cases'] or time.monotonic()-started>=self.s['limits']['optimization_seconds']:
                self.optimizer['status']='limite_alcanzado_sin_recomendacion';break
            intervals=pd.read_csv(dest/'intervalos_sensibles.csv');z=intervals[intervals.pair==pairs[-1]]
            history=intervals.groupby(['interval','pair'],sort=False).max_ratio.max()
            sensitive=[]
            for iid,group in z.groupby('interval'):
                current=group.max_ratio.max()
                prior=history.get((iid,pairs[-2]),float('nan')) if len(pairs)>1 else float('nan')
                if current>1 and current<prior:
                    sensitive.append(iid)
            if not sensitive and ((z.max_ratio>1)|(z.missing>0)).any():
                self.optimizer['status']='sensibilidad_persistente_o_controles_insuficientes'
                break
            if not sensitive:self.optimizer['status']='evidencia_insuficiente_sin_prueba_justificada';break
            target=space/2
            if target<self.s['limits']['minimum_spacing_m']:self.optimizer['status']='espaciamiento_minimo_configurado_sin_estabilizacion';break
            extra_name=self.s['campaign']+'_LOCAL_'+str(len(budget['cases'])+1)
            # All 44 intervals are retained at the finest core spacing; only sensitive ones are refined.
            overrides={}
            if budget['cases']:overrides=campaign.load_config(budget['cases'][-1]['campaign'])[0]['refinements'][0]['overrides'].copy()
            overrides.update({i:min(target,overrides.get(i,250)) for i in sensitive})
            if not campaign.config_path(extra_name).exists():campaign.create_config(extra_name,self.s['model'],[250],self.s['profiles'],overrides=overrides,section_only=True)
            ec,*_=campaign.load_config(extra_name);spec=ec['refinements'][0]
            require(spec['overrides']==overrides,'Prueba adicional existente con decisiones diferentes; no se reutiliza silenciosamente')
            text=geometry.read(model.project(self.folder/self.m['project'])['geom']);new,ledger,inserted=geometry.generate(text,self.s['river'],250,overrides=spec['overrides'],section_only=True)
            require(60+len(inserted)<=self.s['limits']['max_total_sections'],'Prueba local supera límite de secciones; no se ejecuta')
            budget['cases'].append(dict(campaign=extra_name,intervals=sensitive,target_spacing_m=target,reason='excesos o controles faltantes en último refinamiento'));save(budget_path,budget)
            campaign.generate(extra_name);campaign.run(extra_name,timeout=self.s['limits']['case_timeout_seconds'])
            # Rebuild an ensemble, but order this convergence chain by actual refinement sequence.
            tag,available=self.ensemble();ordered=[]
            for cn,case_name in [(c['name'],x) for x in labels]+[(b['campaign'],'R250') for b in budget['cases']]:
                ordered.append(next(a['label'] for a in available if a['campaign']==cn and a['meta']['name']==case_name))
            dest=analysis.analyze(tag,candidates=ordered,mode='convergence');self.analyses.append(str(dest));latest=extra_name;space=target
        save(self.dest/'optimizacion.json',self.optimizer)
        save(self.dest/'analisis_generados.json',self.analyses)
    def report(self):
        if not self.analyses:
            for p in sorted((ROOT/'informes_automaticos').glob('*/analisis_generados.json'),reverse=True):
                valid=[]
                for name in readjson(p):
                    dest=Path(name);contract=dest/'contrato.json'
                    if contract.exists():
                        c=readjson(contract)['config']
                        if c['model']==self.s['model'] and c['profiles']==self.s['profiles']:valid.append(name)
                if valid:self.analyses=valid;break
        states=[]
        try:states=self.core_status()
        except Exception as e:self.errors.append(dict(stage='report/status',error=str(e)))
        core_complete=len(states)==4 and all(x['state']=='completado_verificado' for x in states)
        stages_done={x['stage'] for x in self.log if x['status']=='terminado'}
        complete=core_complete and not self.errors and {'verify','generate','run','analyze','optimize','compare'}<=stages_done
        # Every report has an explicit 44-interval table, even when no calculation exists.
        try:
            self.ensure();rows=[]
            if not self.audit:
                _,out,_,_=self.config();ap=out/'auditoria_ultimo.json'
                if ap.exists():
                    prior=readjson(ap)
                    if prior['settings_hash']==digest(self.s):self.audit=prior['rows']
            for it in self.v['intervals']:
                audit=[r for r in self.audit if r['id']==it['id']]
                rows.append(dict(**it,status='pendiente_sin_auditar' if not audit else ('bloqueado' if any(x['status']=='bloqueado' for x in audit) else 'generacion_revisada_no_convergencia'),details=' | '.join(dict.fromkeys((x['reason'] or x.get('source_warning','')) for x in audit if x['reason'] or x.get('source_warning')))))
            table=pd.DataFrame(rows);table.to_csv(self.dest/'44_intervalos.csv',index=False,encoding='utf-8-sig');table.to_excel(self.dest/'44_intervalos.xlsx',index=False)
        except Exception as e:self.errors.append(dict(stage='report/intervals',error=str(e)));complete=False
        status='ETAPAS AUTOMÁTICAS COMPLETADAS (NO IMPLICA CONVERGENCIA)' if complete else 'CAMPAÑA INCOMPLETA / ETAPAS PENDIENTES O BLOQUEADAS'
        md=f'# Informe automático Biobío\n\n**{status}**\n\nFecha UTC: {now()}\n\nModelo obligatorio: {self.s["model"]}. Geometría SHA-256: `{self.s["geometry_sha256"]}`.\n\nLos pilotos del modelo antiguo sin Levees NO validan esta geometría.\n\n'
        md+='## Estado de los casos obligatorios\n\n|Caso|Estado|\n|---|---|\n'+''.join(f'|{r["case"]}|{r["state"]}|\n' for r in states)
        md+='\n## Limitaciones y bloqueos\n\n'+('\n'.join('- '+x['stage']+': '+x['error'] for x in self.errors) or 'No se registraron errores en las etapas solicitadas; esto no significa convergencia ni ausencia de avisos hidráulicos.')
        md+='\n\nLa tabla 44_intervalos.csv/.xlsx incluye todos los intervalos. auditoria_44_intervalos.csv detalla cada espaciamiento cuando se pidió generar. No se omiten intervalos inseguros ni se usan geometrías parciales como casos completos.\n\n'
        md+='## Recomendación\n\n'+(str(self.optimizer['recommendation']) if self.optimizer.get('recommendation') and core_complete else '**No se ha demostrado una cantidad ni un espaciamiento recomendado.** Se requiere completar las geometrías seguras, los cálculos y la evaluación de estabilización con los seis perfiles.')
        md+='\n\n## Resultados disponibles\n\n'+('\n'.join('- '+p for p in self.analyses) or 'No se produjo un análisis nuevo en esta selección. Los análisis anteriores se conservan en campanas/.')
        md+='\n\n## Reproducibilidad y avisos\n\nConfiguración exacta: configuracion_usada.json. Estados: etapas.json; errores: errores.json. Cada intento conserva entradas, hashes, HDF, recibo, mensajes, diagnostico_nativo.json e indicadores_nativos.csv/avisos_numericos.csv. Los avisos son criterios aplicados a indicadores COM nativos, no una transcripción completa de la tabla Errors/Warnings/Notes de HEC-RAS. Los valores faltantes se declaran; no se reemplazan por cero.\n\nEl plan original 7.00 se ejecuta con motor 6.6 sin reescribirlo. La prueba acotada de compatibilidad está en VALIDACION_LEVEES_02NOV.md. La fuente conserva sus nombres PF 1–PF 6 y caudales exactos. La campaña vigente conserva los Levees en las secciones originales. Las nuevas quedan sin Levees y requieren revisión hidráulica de la extensión lateral del flujo.\n'
        (self.dest/'INFORME_FINAL.md').write_text(md,encoding='utf-8');save(self.dest/'estado_final.json',dict(utc=now(),core_complete=core_complete,requested_stages_ok=not self.errors,complete=complete,recommendation=self.optimizer.get('recommendation') if core_complete else None,states=states,errors=self.errors,analyses=self.analyses))
        page='<!doctype html><meta charset="utf-8"><title>Informe Biobío</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:24px;color:#17324a}pre{white-space:pre-wrap;font:inherit;line-height:1.55}table{border-collapse:collapse;font-size:13px}td,th{padding:8px;border:1px solid #ccd7df}th{background:#e5eef5}</style><h1>Biobío — informe automático</h1><pre>'+html.escape(md)+'</pre>'
        if 'table' in locals():page+='<h2>Los 44 intervalos</h2>'+table.to_html(index=False,escape=True)
        for name in self.analyses:
            ap=Path(name)
            page+='<h2>Resultados disponibles: '+html.escape(ap.parent.parent.name)+'</h2><p><a href="'+html.escape((ap/'resultados.xlsx').as_uri(),quote=True)+'">Abrir tablas Excel</a> · <a href="'+html.escape((ap/'INFORME.md').as_uri(),quote=True)+'">Informe detallado</a></p>'
            metrics=ap/'metricas.csv'
            if metrics.exists():
                mm=pd.read_csv(metrics);page+=mm[(mm.group=='direct_both')&mm.variable.isin(PRIMARY)][['pair','profile','variable','max_abs','RMSE','P95','exceedances']].to_html(index=False,escape=True)
        (self.dest/'INFORME_FINAL.html').write_text(page,encoding='utf-8');(ROOT/'ULTIMO_INFORME.html').write_text(page,encoding='utf-8')
        (ROOT/'ULTIMO_INFORME.txt').write_text(str(self.dest/'INFORME_FINAL.md'),encoding='utf-8')
        print('\n'+status+'\nInforme: '+str(self.dest/'INFORME_FINAL.md'),flush=True);return complete

def execute(action='all',settings=None):
    w=Workflow(settings)
    lock=ROOT/'automatico_en_ejecucion.json';owned=False
    try:
        if lock.exists():
            old=readjson(lock);require(not campaign.process_alive(old['pid']),'Ya hay un flujo automático activo; se evita duplicarlo')
            lock.rename(ROOT/('automatico_interrumpido_'+str(time.time_ns())+'.json'))
        import json
        with lock.open('x',encoding='utf-8') as f:json.dump(dict(pid=os.getpid(),utc=now(),action=action),f)
        owned=True
        steps=['verify','generate','run','analyze','optimize','compare'] if action=='all' else ([] if action=='report' else [action])
        for step in steps:
            fn=(lambda:w.analyze(True)) if step=='compare' else getattr(w,step)
            w.stage(step,fn)
        w.report()
    finally:
        if owned and lock.exists():lock.unlink()
    return 1 if w.errors else 0

def menu():
    print('\nBIOBÍO — MENÚ AUTOMÁTICO | MNMFNuevo_LEVEES_02NOV | 6 perfiles | 44 intervalos\n[1] Verificar modelo\n[2] Generar geometrías\n[3] Ejecutar simulaciones\n[4] Analizar sensibilidad\n[5] Evaluar estabilización\n[6] Generar informes\n[7] EJECUTAR TODO\n[0] Salir')
    op=input('Seleccione una opción: ').strip()
    if op=='0':return 0
    require(op in ACTIONS,'Opción inválida; no se inició ningún cálculo')
    return execute(ACTIONS[op])
