"""Isolated process: COM ownership and outcome receipt survive parent interruption."""
import sys,json,traceback,re
from pathlib import Path
from .common import save,now,sha,require
def main(prj):
    import pythoncom,win32com.client
    prj=Path(prj);folder=prj.parent;pythoncom.CoInitialize();c=None
    from .model import project
    paths=project(prj);hashes={paths[k].name:sha(paths[k]) for k in ['prj','plan','geom','flow']}
    try:
        c=win32com.client.DispatchEx('RAS66.HECRASController');version=str(c.HECRASVersion());require(bool(re.search(r'6\.0?6\b',version)),'Controlador distinto de 6.6')
        c.Project_Open(str(prj));from .model import project
        require(Path(str(c.CurrentPlanFile())).name.lower()==project(prj)['plan'].name.lower(),'Plan activo inesperado')
        result=c.Compute_CurrentPlan(None,None,True);ok=bool(result[0]) if isinstance(result,(tuple,list)) else bool(result)
        require(ok,'HEC-RAS no completó: '+repr(result));require(all(sha(folder/n)==h for n,h in hashes.items()),'HEC-RAS cambió entrada')
        from .diagnostics import collect,text_alerts
        diag=collect(c,prj)
        save(folder/'worker.json',dict(compute_ok=True,version=version,result=str(result),utc=now(),input_hashes=hashes,native_diagnostics_sha256=sha(folder/'diagnostico_nativo.json'),native_alert_count=len(diag['alerts']),compute_alerts=text_alerts(str(result))));print('HEC-RAS finalizado; avisos numéricos:',len(diag['alerts']),flush=True)
    except BaseException as e:
        save(folder/'worker_error.json',dict(error=str(e),traceback=traceback.format_exc(),utc=now()));raise
    finally:
        if c is not None:
            try:c.QuitRas()
            except Exception:pass
        pythoncom.CoUninitialize()
if __name__=='__main__':main(sys.argv[1])
