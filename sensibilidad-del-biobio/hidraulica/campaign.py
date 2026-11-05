from pathlib import Path
import shutil,sys,os,subprocess,json,ctypes,time
import pandas as pd
from .common import *
from . import geometry as geo
from .model import load_model,project,select_flow,verify
from .results import extract
from . import __version__
def config_path(name):return ROOT/'configuraciones'/(safe(name)+'.json')
def create_config(name,model,spacings,profiles=None,reaches=None,intervals=None,output=None,overrides=None,section_only=False):
    folder,m=load_model(model);info=m['inventory'];profiles=profiles or info['profiles']
    require(set(profiles)<=set(info['profiles']) and len(profiles)==len(set(profiles)),'Perfiles desconocidos/duplicados')
    require(spacings and all(float(x)>0 for x in spacings),'Especifique al menos un espaciamiento positivo')
    require(not config_path(name).exists(),'Configuración existente: use nombre nuevo')
    c=dict(schema=1,name=safe(name),model=model,profiles=profiles,refinements=[dict(name='R'+format(float(x),'.12g').replace('.','p').replace('+',''),spacing_m=float(x),reaches=reaches or [],intervals=intervals or [],overrides=overrides or {}) for x in spacings],tolerances=dict(wse_m=.05,velocity_ms=.05,hydraulic_depth_m=.05,froude=.05),output_dir=str(Path(output).resolve()) if output else 'campanas/'+name)
    if section_only:c['levee_policy']='solo_originales'
    require(len({r['name'] for r in c['refinements']})==len(c['refinements']),'Espaciamientos duplicados');save(config_path(name),c);return c
def load_config(name):
    p=Path(name);p=p if p.is_file() else config_path(name);c=readjson(p);safe(c['name']);require(c['schema']==1,'Versión configuración no soportada')
    require(set(c['tolerances'])=={'wse_m','velocity_ms','hydraulic_depth_m','froude'} and all(0<float(x)<float('inf') for x in c['tolerances'].values()),'Tolerancias inválidas')
    folder,m=load_model(c['model']);require(c['profiles'] and len(set(c['profiles']))==len(c['profiles']) and set(c['profiles'])<=set(m['inventory']['profiles']),'Perfiles inválidos')
    out=Path(c['output_dir']);out=out if out.is_absolute() else ROOT/out
    for forbidden in [folder,Path(m['source']).parent,ROOT/'hidraulica']:
        require(out.resolve()!=forbidden.resolve() and forbidden.resolve() not in out.resolve().parents,'Salida no puede estar dentro de fuentes/código')
    from .model import official
    if official() and official()!=m['name']:
        official_meta=readjson(ROOT/'modelos'/official()/'modelo.json')
        if official_meta['inventory']['hashes']!=m['inventory']['hashes']:print('AVISO: campaña histórica de',m['name'],'; NO valida el modelo oficial',official())
    return c,out,m,folder
def generate(name,only=None):
    c,out,m,folder=load_config(name);out.mkdir(parents=True,exist_ok=True);paths=project(folder/m['project']);text=geo.read(paths['geom']);ft=select_flow(geo.read(paths['flow']),c['profiles']);selected=only or [r['name'] for r in c['refinements']]
    require(set(selected)<=set(r['name'] for r in c['refinements']),'Caso seleccionado no definido')
    contract=dict(config=c,source_hashes=m['inventory']['hashes'],generator_version=__version__)
    if (out/'campana.json').exists():
        previous=readjson(out/'campana.json')['contract']
        if previous!=contract:
            require(previous['config']==c and previous['source_hashes']==m['inventory']['hashes'],'Campaña inmutable: configuración o fuente distinta')
            for name in selected:
                case=out/'casos'/safe(name);require((case/'caso.json').exists(),'Generador actualizado: no se agregan casos a campaña histórica; use nombre nuevo')
                meta=readjson(case/'caso.json');check_inputs(case/'entrada',meta);print('Ya generado y conservado con versión histórica:',name)
            return out
    else:save(out/'campana.json',dict(contract=contract,created_utc=now(),model=m['name']))
    for spec in c['refinements']:
        if spec['name'] not in selected:continue
        case=out/'casos'/safe(spec['name']);fingerprint=digest(dict(contract=contract,spec=spec))
        if (case/'caso.json').exists():
            meta=readjson(case/'caso.json');require(meta['fingerprint']==fingerprint,'Caso existente con contrato distinto');check_inputs(case/'entrada',meta);print('Ya generado:',spec['name']);continue
        require(not case.exists(),'Directorio incompleto existente: conservarlo y usar nueva campaña')
        try:gt,ledger,inserted=geo.generate(text,m['river'],spec['spacing_m'],spec.get('reaches'),spec.get('intervals'),spec.get('overrides'),section_only=c.get('levee_policy')=='solo_originales')
        except Exception as e:
            save(out/('BLOQUEADO_'+spec['name']+'.json'),dict(error=str(e),spec=spec,utc=now()));raise
        inp=case/'entrada';inp.mkdir(parents=True)
        for k in ['prj','plan']:shutil.copy2(paths[k],inp/paths[k].name)
        if not inserted:shutil.copy2(paths['geom'],inp/paths['geom'].name)
        else:geo.write(inp/paths['geom'].name,gt)
        if c['profiles']==m['inventory']['profiles']:shutil.copy2(paths['flow'],inp/paths['flow'].name)
        else:geo.write(inp/paths['flow'].name,ft)
        meta=dict(name=spec['name'],fingerprint=fingerprint,project=m['project'],profiles=c['profiles'],created_utc=now(),geometry_count=len(m['inventory']['sections'])+len(inserted),base_count=m['inventory']['base_count'],river_count=m['inventory']['base_count']+len(inserted),inserted_count=len(inserted),input_hashes={p.name:sha(p) for p in inp.iterdir()},spec=spec)
        save(case/'caso.json',meta);pd.DataFrame(ledger).to_csv(case/'intervalos.csv',index=False,encoding='utf-8-sig');pd.DataFrame(inserted).to_csv(case/'interpoladas.csv',index=False,encoding='utf-8-sig');print('Generado:',spec['name'],meta['river_count'],'XS río /',meta['geometry_count'],'red')
    return out
def check_inputs(folder,meta):
    require(all((folder/n).is_file() and sha(folder/n)==h for n,h in meta['input_hashes'].items()),'Entradas alteradas: '+str(folder))
def process_alive(pid):
    if os.name=='nt':
        k=ctypes.WinDLL('kernel32',use_last_error=True);k.OpenProcess.restype=ctypes.c_void_p;k.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)];k.CloseHandle.argtypes=[ctypes.c_void_p]
        h=k.OpenProcess(0x1000,False,int(pid))
        if not h:return False
        code=ctypes.c_ulong();ok=k.GetExitCodeProcess(h,ctypes.byref(code));k.CloseHandle(h);return bool(ok and code.value==259)
    try:os.kill(int(pid),0);return True
    except OSError:return False
def validate_attempt(attempt,meta,sections):
    check_inputs(attempt,meta);df,info=extract(attempt/meta['project'],sections);require(info['profiles']==meta['profiles'],'Perfiles del intento difieren del contrato')
    require(len(df)//len(info['profiles'])==meta['geometry_count'],'Conteo HDF no corresponde')
    if (attempt/'worker.json').exists():
        worker=readjson(attempt/'worker.json')
        if worker.get('native_diagnostics_sha256'):
            dp=attempt/'diagnostico_nativo.json';require(dp.is_file() and sha(dp)==worker['native_diagnostics_sha256'],'Diagnóstico nativo alterado/ausente')
            diag=readjson(dp);require(diag['hdf_sha256']==info['hdf_sha256'] and diag['profiles']==info['profiles'] and len(diag['rows'])==len(df),'Diagnóstico no corresponde al HDF/perfiles')
            info['native_alert_count']=len(diag['alerts'])
    return df,info
def receipt(case,attempt,meta,sections,recovered=False):
    df,info=validate_attempt(attempt,meta,sections)
    require((attempt/'worker.json').exists() and readjson(attempt/'worker.json').get('compute_ok') is True,'Falta recibo del controlador; se conserva HDF pero no se acepta como corrida verificada')
    r=dict(status='completed',attempt=attempt.name,fingerprint=meta['fingerprint'],hdf_sha256=info['hdf_sha256'],completed_utc=now(),recovered=recovered,profiles=info['profiles'],native_section_warnings_reviewed=False)
    r['native_indicator_alert_count']=info.get('native_alert_count')
    from .diagnostics import text_alerts
    r['compute_message_alerts']=text_alerts(info['messages'])
    save(case/'completado.json',r);df.to_csv(attempt/'hidraulica.csv',index=False,encoding='utf-8-sig');(attempt/'mensajes.txt').write_text(info['messages'],encoding='utf-8')
    dp=attempt/'diagnostico_nativo.json'
    if dp.exists():
        diag=readjson(dp);pd.DataFrame(diag['rows']).to_csv(attempt/'indicadores_nativos.csv',index=False,encoding='utf-8-sig');pd.DataFrame(diag['alerts']).to_csv(attempt/'avisos_numericos.csv',index=False,encoding='utf-8-sig')
        print('AVISOS numéricos:',len(diag['alerts']),'; ver avisos_numericos.csv. No equivale a tabla completa de avisos UI.')
    return r
def completed(case,meta,sections):
    p=case/'completado.json'
    if not p.exists():return None
    r=readjson(p);require(r['fingerprint']==meta['fingerprint'],'Recibo incompatible');attempt=case/'intentos'/r['attempt'];df,info=validate_attempt(attempt,meta,sections);require(info['hdf_sha256']==r['hdf_sha256'],'HDF completado alterado');return attempt
def run(name,only=None,timeout=600):
    c,out,m,folder=load_config(name);require((out/'campana.json').exists(),'Primero genere geometrías');require(readjson(out/'campana.json')['contract']['config']==c,'Configuración modificada: use una campaña nueva')
    selected=only or [x['name'] for x in c['refinements']];require(set(selected)<=set(x['name'] for x in c['refinements']),'Caso desconocido')
    failures=[]
    for name in selected:
        case=out/'casos'/safe(name)
        guard=case/'supervisor.json';owned=False
        try:
            require(case.is_dir(),'Caso no generado: '+name)
            if guard.exists():
                old=readjson(guard);require(not process_alive(old['pid']),'Otro supervisor está ejecutando este caso')
                guard.rename(case/('supervisor_interrumpido_'+str(time.time_ns())+'.json'))
            try:
                with guard.open('x',encoding='utf-8') as f:json.dump(dict(pid=os.getpid(),utc=now()),f)
                owned=True
            except FileExistsError:raise ValidationError('Otro supervisor tomó el caso; reintente cuando termine')
            meta=readjson(case/'caso.json');check_inputs(case/'entrada',meta);sections=m['inventory']['sections']
            if completed(case,meta,sections):print('OMITIDO: completado y verificado',name);continue
            locks=list((case/'intentos').glob('*/ejecutando.json')) if (case/'intentos').exists() else []
            for lp in locks:
                lock=readjson(lp);require(not process_alive(lock['pid']),'Caso ejecutándose (PID '+str(lock['pid'])+'); espere o cierre esa corrida antes de reanudar')
            recovered=False
            for attempt in sorted((case/'intentos').glob('*')) if (case/'intentos').exists() else []:
                if not attempt.is_dir():continue
                try:receipt(case,attempt,meta,sections,True);print('RECUPERADO sin recalcular:',name);recovered=True;break
                except Exception as e:save(attempt/'recuperacion.json',dict(status='no_valido_conservado',error=str(e),utc=now()))
            if recovered:continue
            attempts=case/'intentos';attempts.mkdir(exist_ok=True);attempt=attempts/f'{len(list(attempts.iterdir()))+1:04d}';attempt.mkdir()
            for src in (case/'entrada').iterdir():shutil.copy2(src,attempt/src.name)
            workerlog=attempt/'worker.log'
            with open(workerlog,'w',encoding='utf-8') as log:
                proc=subprocess.Popen([sys.executable,'-m','hidraulica.worker',str(attempt/meta['project'])],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
                save(attempt/'ejecutando.json',dict(pid=proc.pid,started_utc=now()))
                try:code=proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:raise ValidationError('Tiempo excedido. No se mata HEC-RAS ni se inicia otro intento mientras el worker siga activo. Ver '+str(workerlog))
            require(code==0,'HEC-RAS/worker falló. Ver '+str(workerlog));receipt(case,attempt,meta,sections);print('COMPLETADO:',name)
        except KeyboardInterrupt:
            print('Interrumpido. Se conservan intentos; no se inicia una corrida duplicada mientras la anterior siga activa.');raise
        except Exception as e:
            failures.append(dict(case=name,error=str(e)));save(case/'ultimo_error.json',dict(error=str(e),utc=now()));print('ERROR',name,str(e))
        finally:
            if owned and guard.exists():guard.unlink()
    require(not failures,'Casos con error: '+str(failures))
def status(name):
    c,out,m,folder=load_config(name);rows=[]
    for spec in c['refinements']:
        case=out/'casos'/spec['name'];state='no_generado'
        if (case/'caso.json').exists():
            meta=readjson(case/'caso.json')
            try:check_inputs(case/'entrada',meta);state='completado_verificado' if completed(case,meta,m['inventory']['sections']) else 'pendiente_o_interrumpido'
            except Exception as e:state='invalido: '+str(e)
        rows.append(dict(case=spec['name'],state=state))
    return rows
