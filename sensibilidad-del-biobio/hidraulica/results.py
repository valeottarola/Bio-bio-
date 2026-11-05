from pathlib import Path
import numpy as np,pandas as pd,h5py
from .common import *
from . import geometry as geo,levees
from .model import project,flows
BASE='Results/Steady/Output/Output Blocks/Base Output/Steady Profiles'
XS=BASE+'/Cross Sections'
VARIABLES={'wse_m':'Water Surface','velocity_ms':'Additional Variables/Velocity Total','hydraulic_depth_m':'Additional Variables/Hydraulic Depth Total','area_m2':'Additional Variables/Area Flow Total','wet_area_m2':'Additional Variables/Area including Ineffective Total','radius_m':'Additional Variables/Hydraulic Radius Total','friction_slope':'Additional Variables/Friction Slope','flow_m3s':'Additional Variables/Flow Total'}
PRIMARY=['wse_m','velocity_ms','hydraulic_depth_m','froude']
def extract(prj,base_sections=None):
    p=project(prj);gt=geo.read(p['geom']);_,rr,_=geo.parse(gt);gi={};names,entries,_=flows(geo.read(p['flow']))
    basekeys={key(x['river'],x['reach'],x['station']) for x in base_sections} if base_sections else None
    for r in rr:
        chain=0.;current=None
        for x in r['xs']:
            k=key(x['river'],x['reach'],x['station']);require(k not in gi,'Identificador de sección duplicado')
            e=next((e for e in entries if key(e['river'],e['reach'],e['station'])==k),None)
            if e:current=e['values']
            require(current is not None,'Caudal no cubre sección '+str(k));gi[k]=(x,chain,current);chain+=x['lengths'][1]
    require(p['hdf'].is_file(),'No existe HDF de resultados: '+str(p['hdf']))
    with h5py.File(p['hdf'],'r') as f:
        require('HEC-RAS 6.6' in decode(f.attrs.get('File Version','')),'HDF no calculado con HEC-RAS 6.6')
        require(decode(f.attrs.get('Units System',''))=='SI Units','HDF no SI')
        pn=[decode(x) for x in f[BASE+'/Profile Names'][()]];require(pn==[n[:15] for n in names],'HDF no corresponde a perfiles esperados')
        meta=f['Results/Steady/Output/Geometry Info/Cross Section Attributes'][()];keys=[key(x['River'],x['Reach'],x['Station']) for x in meta]
        require(len(keys)==len(gi) and set(keys)==set(gi),'HDF no corresponde a estaciones de geometría')
        att=f['Geometry/Cross Sections/Attributes'][()];info=f['Geometry/Cross Sections/Station Elevation Info'][()];vals=f['Geometry/Cross Sections/Station Elevation Values'][()]
        ninfo=f["Geometry/Cross Sections/Manning's n Info"][()];nvals=f["Geometry/Cross Sections/Manning's n Values"][()]
        for j,x in enumerate(att):
            z=gi[key(x['River'],x['Reach'],x['RS'])][0];start,count=map(int,info[j,:2]);xy=vals[start:start+count]
            require(xy.shape==z['xy'].shape and np.allclose(xy,z['xy'],atol=.002,rtol=0),'Topografía HDF distinta')
            require(np.allclose([x['Len Left'],x['Len Channel'],x['Len Right']],z['lengths'],atol=.003,rtol=0),'Longitudes HDF distintas')
            require(np.allclose([x['Left Bank'],x['Right Bank']],z['banks'],atol=.002,rtol=0),'Bancos HDF distintos')
            for side,pair in zip(('Left','Right'),levees.read(z)):
                fields=[side+' Levee Sta',side+' Levee Elev']
                if not any(n in att.dtype.names for n in fields):
                    require(pair is None and not any(any(levees.read(item[0])) for item in gi.values()),'HDF sin atributos Levee verificables para geometría con Levees')
                    continue
                require(all(n in att.dtype.names for n in fields),'Atributos Levee HDF incompletos')
                actual=np.array([x[n] for n in fields],float)
                require(np.isnan(actual).all() if pair is None else np.isfinite(actual).all() and np.allclose(actual,pair,atol=.003,rtol=0),'Levee HDF distinto: '+z['reach']+' / '+z['station']+' / '+side)
            coeff=list(map(float,geo.g.field(z['block'],'Exp/Cntr').split(',')))
            require(np.allclose([x['Expan'],x['Contr']],coeff,atol=1e-6,rtol=0),'Coeficientes expansión/contracción HDF distintos')
            ni,nc=map(int,ninfo[j,:2]);mn=geo.manning(z);hn=nvals[ni:ni+nc]
            require(hn.shape==(len(mn),2) and np.allclose(hn[:,0],mn[:,0],atol=.002,rtol=0) and np.allclose(hn[:,1],mn[:,1],atol=1e-7,rtol=0),'Manning HDF distinto')
        arrays={};missing=[]
        for v,path in VARIABLES.items():
            if XS+'/'+path in f:arrays[v]=np.array(f[XS+'/'+path],float)
            else:
                require(v not in PRIMARY and v!='flow_m3s','Variable principal HDF ausente: '+v);arrays[v]=np.full((len(names),len(keys)),np.nan);missing.append(v)
        require(all(y.shape==(len(names),len(keys)) for y in arrays.values()),'Dimensiones HDF incorrectas')
        require(all(np.isfinite(arrays[v]).all() for v in PRIMARY if v!='froude') and (arrays['hydraulic_depth_m']>0).all(),'Valores principales no finitos/profundidad no positiva')
        arrays['froude']=abs(arrays['velocity_ms'])/np.sqrt(9.80665*arrays['hydraulic_depth_m'])
        messages='\n'.join(decode(x) for x in f['Results/Summary/Compute Messages (text)'][()]);require('Finished Steady Flow Simulation' in messages,'HDF no confirma finalización')
        rows=[]
        for ip,name in enumerate(names):
            for i,k in enumerate(keys):
                x,chain,q=gi[k];require(abs(arrays['flow_m3s'][ip,i]-q[ip])<max(.001,1e-5*q[ip]),'Caudal HDF distinto de f01')
                row=dict(profile=name,river=x['river'],reach=x['reach'],station=x['station'],chainage_m=chain,base=k in basekeys if basekeys is not None else True)
                row.update({v:float(y[ip,i]) for v,y in arrays.items()});row['regime_derived']='supercritical' if row['froude']>1 else 'subcritical';rows.append(row)
    return pd.DataFrame(rows),dict(hdf_sha256=sha(p['hdf']),messages=messages,missing_complementary=missing,profiles=names)
