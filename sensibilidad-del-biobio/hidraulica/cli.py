import argparse,sys,traceback,importlib.util
from pathlib import Path
from .common import *
from . import model,campaign,analysis,geometry
def split(s):return [x.strip() for x in s.split(',') if x.strip()] if s else []
def doctor(com=False):
    modules=['numpy','pandas','h5py','matplotlib','openpyxl','win32com.client'];missing=[]
    for name in modules:
        try:__import__(name)
        except ImportError:missing.append(name)
    info=dict(python=sys.executable,version=sys.version,missing=missing,root=str(ROOT),HECRAS66_registered=False)
    if sys.platform=='win32':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,r'RAS66.HECRASController\CLSID'):info['HECRAS66_registered']=True
        except OSError:pass
    if com:
        import win32com.client
        c=win32com.client.DispatchEx('RAS66.HECRASController')
        try:info['HECRAS_version']=str(c.HECRASVersion())
        finally:c.QuitRas()
    print(json.dumps(info,ensure_ascii=False,indent=2));return info
def choose(items,title,multiple=False):
    require(items,'No hay elementos. Use [8] para registrar modelo/crear configuración.')
    print(title)
    for i,x in enumerate(items,1):print(f'  {i}. {x}')
    s=input('Números separados por coma; Enter=todos: ' if multiple else 'Número: ').strip()
    if multiple and not s:return list(items)
    idx=[int(x)-1 for x in split(s)];require(idx and all(0<=i<len(items) for i in idx),'Selección inválida')
    return [items[i] for i in idx] if multiple else items[idx[0]]
def configs():return sorted(p.stem for p in (ROOT/'configuraciones').glob('*.json') if not p.name.endswith('.propuesta.json'))
def models():return sorted(p.parent.name for p in (ROOT/'modelos').glob('*/modelo.json'))

def audit_model(name,spacing=1000):
    from .levees import audit
    import pandas as pd
    folder,m=model.load_model(name);rows=audit(geometry.read(model.project(folder/m['project'])['geom']),m['river'],spacing)
    dest=ROOT/'diagnosticos'/('intervalos_'+name+'_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'));dest.mkdir(parents=True)
    save(dest/'intervalos.json',rows);pd.DataFrame(rows).to_csv(dest/'intervalos.csv',index=False,encoding='utf-8-sig')
    for r in rows:print(r['id'],r['reach'],r['upstream'],'→',r['downstream'],r['status'],r['reason'])
    print('Auditoría guardada:',dest);return dest
def select_config():return choose(configs(),'Configuraciones guardadas')
def select_cases(name):
    c,*_=campaign.load_config(name);return choose([r['name'] for r in c['refinements']],'Casos',True)
def create_wizard():
    print('Modelo oficial actual:',model.official() or 'no definido')
    mname=choose(models(),'Modelo');folder,m=model.load_model(mname);profiles=choose(m['inventory']['profiles'],'Perfiles',True)
    spacings=[float(x) for x in split(input('Espaciamientos en m [1000,500,250,125] o personalizados: '))];require(spacings,'Faltan espaciamientos')
    reaches=choose(sorted({x['reach'] for x in m['inventory']['intervals']}),'Reach (Enter=todos)',True)
    for it in m['inventory']['intervals']:print(it['id'],it['reach'],it['upstream'],'→',it['downstream'],it['length_m'],'m')
    intervals=split(input('IDs de intervalos (p.ej. I003,I004; Enter=todos los Reach elegidos): '));name=input('Nombre de campaña/configuración: ').strip();output=input('Carpeta de resultados (Enter=campanas/nombre): ').strip() or None
    c=campaign.create_config(name,mname,spacings,profiles,reaches,intervals,output);print('Guardada:',campaign.config_path(name));return c
def manage():
    print('1 Registrar modelo | 2 Crear configuración | 3 Ver configuración | 4 Duplicar configuración | 5 Diagnóstico local | 6 Listar modelos | 7 Elegir modelo oficial | 8 Auditar intervalos/Levees')
    op=input('Opción: ').strip()
    if op=='1':
        p=input('Ruta completa del .prj: ').strip().strip('"');name=input('Nombre del modelo: ').strip();river=input('Nombre exacto del río a refinar: ').strip();prov=input('JSON de procedencia opcional (Enter=sin información): ').strip();m=model.import_model(p,name,river,prov or None);print('Registrado:',m['name'])
    elif op=='2':create_wizard()
    elif op=='3':print(json.dumps(campaign.load_config(select_config())[0],indent=2,ensure_ascii=False))
    elif op=='4':
        c,*_=campaign.load_config(select_config());new=safe(input('Nuevo nombre: ').strip());require(not campaign.config_path(new).exists(),'Nombre existente');c['name']=new;c['output_dir']='campanas/'+new
        sp=input('Nuevos espaciamientos separados por coma (Enter=conservar): ').strip()
        if sp:c['refinements']=[dict(name='R'+format(float(x),'.12g').replace('.','p').replace('+',''),spacing_m=float(x),reaches=[],intervals=[],overrides={}) for x in split(sp)]
        tol=input('Tolerancia común (Enter=conservar; cambie solo con justificación): ').strip()
        if tol:
            reason=input('Justificación del cambio: ').strip();require(reason,'Debe documentar el cambio');c['tolerances']={k:float(tol) for k in c['tolerances']};c['tolerance_change_reason']=reason
        save(campaign.config_path(new),c);print('Guardada:',new)
    elif op=='5':doctor()
    elif op=='6':print('\n'.join(models()))
    elif op=='7':model.set_official(choose(models(),'Nuevo modelo oficial (conserva históricos)'))
    elif op=='8':audit_model(choose(models(),'Modelo'),float(input('Espaciamiento a verificar, m: ')))
    else:raise ValidationError('Opción inválida')
def analysis_dirs(name):
    _,out,*_=campaign.load_config(name);return sorted(str(p.parent) for p in (out/'analisis').glob('*/contrato.json'))
def advanced_menu():
    print('MODO AVANZADO — configuración interactiva')
    print('MODELO OFICIAL:',model.official() or 'no definido')
    if model.official():print('Los pilotos de otras versiones no validan este modelo. [8] → [8] permite auditar Levees antes de generar.')
    while True:
        print('\nHIDRÁULICA FASE 2 — programa local, sin servicios de nube\n[1] Verificar proyecto\n[2] Generar geometrías\n[3] Ejecutar simulaciones\n[4] Analizar sensibilidad\n[5] Evaluar convergencia / proponer optimización local\n[6] Comparar escenarios seleccionados\n[7] Generar informes\n[8] Gestionar modelos y configuraciones\n[0] Salir')
        try:
            op=input('Opción: ').strip()
            if op=='0':return
            if op=='1':
                name=choose(models(),'Modelo');folder,m=model.load_model(name);v=model.verify(folder/m['project'],m['river']);print('BASE:',v['base_count'],'AFLUENTES:',v['tributary_count'],'INTERVALOS:',len(v['intervals']),'PERFILES:',v['profiles'])
                from .results import extract
                df,info=extract(folder/m['project'],v['sections']);print('HDF verificado:',len(df),'registros')
            elif op=='2':
                if input('1 Usar configuración guardada | 2 Crear nueva: ').strip()=='2':c=create_wizard();name=c['name']
                else:name=select_config()
                campaign.generate(name,select_cases(name))
            elif op=='3':
                name=select_config();cases=select_cases(name);print('Se ejecutarán/verificarán solo:',cases)
                if input('Escriba EJECUTAR para continuar: ').strip().upper()=='EJECUTAR':campaign.run(name,cases)
            elif op in ['4','6']:
                name=select_config();c,*_=campaign.load_config(name);labels=['BASE']+[r['name'] for r in c['refinements']];ref=choose(labels,'Referencia');cand=choose([x for x in labels if x!=ref],'Candidatos',True);print(analysis.analyze(name,ref,cand))
            elif op=='5':
                name=select_config();action=input('1 Comparar refinamientos sucesivos | 2 Proponer configuración local desde análisis: ').strip()
                if action=='2':print(analysis.propose(name,choose(analysis_dirs(name),'Análisis'),input('Nombre de nueva configuración: ').strip()))
                else:
                    print('Seleccione los casos EN ORDEN de grueso a fino; se conservará ese orden.');cc,*_=campaign.load_config(name);ordered=choose(['BASE']+[r['name'] for r in cc['refinements']],'Orden de refinamientos',True);print(analysis.analyze(name,candidates=ordered,mode='convergence'))
            elif op=='7':name=select_config();print(analysis.export_report(choose(analysis_dirs(name),'Análisis a exportar')))
            elif op=='8':manage()
            else:print('Opción no válida.')
        except EOFError:return
        except KeyboardInterrupt:print('\nOperación interrumpida; archivos conservados. Use [3] para reanudar.')
        except Exception as e:log_error(e);print('ERROR:',e)
def log_error(e):
    ROOT.mkdir(parents=True,exist_ok=True);save(ROOT/'diagnosticos'/('error_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json'),dict(utc=now(),error=str(e),traceback=traceback.format_exc()))
def main(argv=None):
    p=argparse.ArgumentParser(description='Sensibilidad hidráulica local HEC-RAS 6.6');sub=p.add_subparsers(dest='command')
    sub.add_parser('menu');d=sub.add_parser('doctor');d.add_argument('--com',action='store_true')
    sub.add_parser('advanced')
    z=sub.add_parser('auto');z.add_argument('action',nargs='?',default='all',choices=['verify','generate','run','analyze','optimize','compare','report','all']);z.add_argument('--settings')
    z=sub.add_parser('set-official');z.add_argument('model')
    z=sub.add_parser('audit-intervals');z.add_argument('model');z.add_argument('--spacing',type=float,default=1000)
    v=sub.add_parser('verify');v.add_argument('--project');v.add_argument('--model');v.add_argument('--river',default='Biobio');v.add_argument('--hdf',action='store_true')
    i=sub.add_parser('import-model');i.add_argument('--project',required=True);i.add_argument('--name',required=True);i.add_argument('--river',required=True);i.add_argument('--provenance')
    cc=sub.add_parser('configure');cc.add_argument('--name',required=True);cc.add_argument('--model',required=True);cc.add_argument('--spacings',required=True);cc.add_argument('--profiles');cc.add_argument('--reaches');cc.add_argument('--intervals');cc.add_argument('--output')
    for cmd in ['generate','run','status']:
        z=sub.add_parser(cmd);z.add_argument('config');z.add_argument('--cases');
        if cmd=='run':z.add_argument('--timeout',type=int,default=600)
    for cmd in ['analyze','convergence','compare']:
        z=sub.add_parser(cmd);z.add_argument('config');z.add_argument('--reference',default='BASE');z.add_argument('--candidates')
    z=sub.add_parser('report');z.add_argument('analysis_dir')
    z=sub.add_parser('optimize');z.add_argument('config');z.add_argument('--analysis-dir',required=True);z.add_argument('--new-name',required=True)
    a=p.parse_args(argv)
    try:
        if a.command in [None,'menu']:
            from .automatic import menu
            return menu()
        elif a.command=='advanced':advanced_menu()
        elif a.command=='auto':
            from .automatic import execute
            return execute(a.action,a.settings)
        elif a.command=='doctor':doctor(a.com)
        elif a.command=='set-official':model.set_official(a.model)
        elif a.command=='audit-intervals':audit_model(a.model,a.spacing)
        elif a.command=='verify':
            if a.model:folder,m=model.load_model(a.model);prj=folder/m['project'];river=m['river']
            else:require(a.project,'Indique --project o --model');prj=a.project;river=a.river
            v=model.verify(prj,river);print(json.dumps(dict(base=v['base_count'],tributaries=v['tributary_count'],reaches=v['reach_count'],intervals=len(v['intervals']),profiles=v['profiles']),indent=2))
            if a.hdf:
                from .results import extract
                d,m=extract(prj,v['sections']);print('HDF OK:',len(d),'registros')
        elif a.command=='import-model':print(model.import_model(a.project,a.name,a.river,a.provenance)['name'])
        elif a.command=='configure':print(campaign.create_config(a.name,a.model,[float(x) for x in split(a.spacings)],split(a.profiles),split(a.reaches),split(a.intervals),a.output)['name'])
        elif a.command=='generate':print(campaign.generate(a.config,split(a.cases)))
        elif a.command=='run':campaign.run(a.config,split(a.cases),a.timeout)
        elif a.command=='status':print(json.dumps(campaign.status(a.config),indent=2))
        elif a.command in ['analyze','compare','convergence']:print(analysis.analyze(a.config,a.reference,split(a.candidates),'convergence' if a.command=='convergence' else 'sensitivity'))
        elif a.command=='report':print(analysis.export_report(a.analysis_dir))
        elif a.command=='optimize':print(analysis.propose(a.config,a.analysis_dir,a.new_name)['name'])
        return 0
    except KeyboardInterrupt:print('Interrumpido; reanude con run.');return 130
    except Exception as e:log_error(e);print('ERROR:',e);return 1
if __name__=='__main__':sys.exit(main())
