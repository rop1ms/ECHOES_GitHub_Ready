"""One-time catalog authoring; generated translations ship locally, no runtime service."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import ast, json, re, sys, time
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
sys.stdout.reconfigure(encoding='utf-8')
import i18n
i18n._load('en')
SKIP={'i18n.py','i18n_en.py','i18n_en_extra.py','artist_identity.py','dsl_docs.py','dsl_docs_en.py',
      'studio_help.py','studio_help_en.py','dsl_lang.py','dsl_lib.py','dsl_runtime.py'}
exact, templates, origins={}, {}, {}
def meaningful(text):
    return bool(i18n._CYR.search(text) and 1<len(text)<12000 and not any(x in text for x in ('(?','\\b','\\s','\\d','\\w')))
for file in sorted(list((ROOT/'app').glob('*.py')) + list((ROOT/'launcher').glob('*.py'))):
    if file.name in SKIP: continue
    source=file.read_text('utf-8'); tree=ast.parse(source)
    parents={id(child):parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    joined={id(v) for n in ast.walk(tree) if isinstance(n,ast.JoinedStr) for v in n.values}
    for node in ast.walk(tree):
        template=isinstance(node,ast.JoinedStr)
        if template:
            pieces=[]; index=0
            for value in node.values:
                if isinstance(value,ast.Constant): pieces.append(str(value.value).replace('{','{{').replace('}','}}'))
                else: pieces.append('{'+str(index)+'}'); index+=1
            text=''.join(pieces)
        elif isinstance(node,ast.Constant) and isinstance(node.value,str) and id(node) not in joined:
            if isinstance(parents.get(id(node)),ast.Expr): continue
            text=node.value
        else: continue
        if not meaningful(text): continue
        ancestor=node; skip=False
        for _ in range(8):
            ancestor=parents.get(id(ancestor))
            if ancestor is None:break
            if isinstance(ancestor,ast.Call):
                name=ancestor.func.id if isinstance(ancestor.func,ast.Name) else ancestor.func.attr if isinstance(ancestor.func,ast.Attribute) else ''
                if name in ('print','log','_log','_net_log','debug','compile','findall','search','sub','match','fullmatch'):
                    skip=True
                break
        if skip:continue
        existing=i18n._EXACT.get(text) if template else i18n.tr(text)
        if (template and any(rx.fullmatch(text) and not i18n._CYR.search(en) for _,rx,en,_ in i18n._TEMPLATES)) or (existing is not None and existing!=text and not i18n._CYR.search(existing)): continue
        (templates if template else exact).setdefault(text,None)
        origins.setdefault(text,[file.name,node.lineno])

out=ROOT/'app/assets/i18n_en_complete.json'
catalog=json.loads(out.read_text('utf-8')) if out.exists() else {'exact':{},'templates':{}}
todo=[(kind,key) for kind,data in (('exact',exact),('templates',templates)) for key in data if key not in catalog[kind]]
(ROOT/'qa/results/i18n_catalog_sources.json').write_text(json.dumps(origins,ensure_ascii=False,indent=2),'utf-8')
print('Existing',sum(map(len,catalog.values())),'To translate',len(todo),flush=True)
if '--audit-only' in sys.argv:sys.exit(0)

batches=[]; batch=[]; chars=0
for item in todo:
    if chars+len(item[1])>2300 and batch:batches.append(batch);batch=[];chars=0
    batch.append(item);chars+=len(item[1])+40
if batch:batches.append(batch)

def translate(batch):
    raw='\n\n'.join(f'__ECHOES_ITEM_{i:04d}__\n'+re.sub(r'\{(\d+)\}',r'[[ECHOES_ARG_\1]]',key.strip()) for i,(_,key) in enumerate(batch))
    for attempt in range(5):
        try:
            response=requests.get('https://translate.googleapis.com/translate_a/single',params={
                'client':'gtx','sl':'ru','tl':'en','dt':'t','q':raw},timeout=40)
            response.raise_for_status()
            translated=''.join(part[0] or '' for part in response.json()[0])
            parts=re.split(r'__ECHOES_ITEM_(\d{4})__\s*',translated)
            found={int(parts[i]):parts[i+1].strip() for i in range(1,len(parts)-1,2)}
            if len(found)!=len(batch):raise ValueError('translation markers changed')
            result=[]
            for i,(kind,key) in enumerate(batch):
                value=re.sub(r'\[\[\s*ECHOES_ARG_(\d+)\s*\]\]',r'{\1}',found[i])
                if sorted(re.findall(r'\{\d+\}',key))!=sorted(re.findall(r'\{\d+\}',value)):raise ValueError('placeholder changed')
                value=re.sub(r'Эхос|ЭХОС','ECHOES',value)
                if i18n._CYR.search(value):raise ValueError('untranslated Cyrillic')
                prefix=re.match(r'^\s*',key).group();suffix=re.search(r'\s*$',key).group()
                result.append((kind,key,prefix+value+suffix))
            return result
        except Exception as error:
            if attempt==4:return [('error',str(error),batch)]
            time.sleep(1+attempt*2)

errors=[]
with ThreadPoolExecutor(max_workers=3) as pool:
    for completed,future in enumerate(as_completed([pool.submit(translate,b) for b in batches]),1):
        for kind,key,value in future.result():
            if kind=='error':errors.append({'error':key,'batch':value})
            else:catalog[kind][key]=value
        out.write_text(json.dumps(catalog,ensure_ascii=False,indent=2),'utf-8')
        if completed%10==0:print('Batches',completed,'/',len(batches),'entries',sum(map(len,catalog.values())),'errors',len(errors),flush=True)
(ROOT/'qa/results/i18n_translation_errors.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2),'utf-8')
print('Done',sum(map(len,catalog.values())),'errors',len(errors),flush=True)
