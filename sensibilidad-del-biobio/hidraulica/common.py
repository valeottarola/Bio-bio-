from pathlib import Path
import hashlib,json,os,re,tempfile,datetime
ROOT=Path(os.environ.get('HIDRAULICA_HOME',str(Path(__file__).resolve().parents[1])))
class ValidationError(ValueError): pass
def require(ok,msg):
    if not ok: raise ValidationError(msg)
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1048576),b''): h.update(b)
    return h.hexdigest()
def digest(obj): return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
def readjson(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def save(p,obj):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=p.name+'.',dir=p.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(obj,f,ensure_ascii=False,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
        os.replace(tmp,p)
    finally:
        if Path(tmp).exists():Path(tmp).unlink()
def safe(name):
    require(bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,69}',name)) and name not in ['.','..'] and name.upper().split('.')[0] not in ['CON','PRN','AUX','NUL']+[f'{x}{i}' for x in ['COM','LPT'] for i in range(1,10)],'Nombre inválido: use letras, números, guion o guion bajo.')
    return name
def decode(x):return x.decode('utf-8',errors='replace').strip().rstrip('\x00') if isinstance(x,bytes) else str(x).strip()
def key(r,reach,station):return decode(r),decode(reach),f'{float(decode(station).rstrip("*")):.8f}'
