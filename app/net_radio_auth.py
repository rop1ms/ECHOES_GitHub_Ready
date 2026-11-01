"""Signed radio-host permits issued by the existing trusted administrator/moderators."""
import json
import math
import secrets
import time

def load(node):
    if '_radio_permits' not in node.__dict__:
        try: raw=json.loads((node.base/'radio_permits.json').read_text('utf-8'))
        except (OSError,ValueError): raw={}
        node._radio_permits={k:v for k,v in raw.items() if valid(node,v)} if isinstance(raw,dict) else {}
    return node._radio_permits

def valid(node,decision):
    from net_core import verify_obj,normalize_code
    if not isinstance(decision,dict) or decision.get('t')!='echoes-radio-permit':return False
    if len(json.dumps(decision))>3000 or type(decision.get('allow')) is not bool:return False
    ts=decision.get('ts')
    return (isinstance(ts,(float,int)) and not isinstance(ts,bool) and math.isfinite(ts) and 0<ts<=time.time()+60
        and bool(normalize_code(decision.get('target'))) and node.role(decision.get('by')) in ('admin','mod')
        and verify_obj(decision,'by'))

def take(node,decision):
    if not valid(node,decision):return False
    records=load(node);target=decision['target'];old=records.get(target)
    if old and (old.get('ts',0),old.get('id','')) >= (decision['ts'],decision.get('id','')):return False
    records[target]=decision
    while len(records)>1000:records.pop(min(records,key=lambda key:records[key]['ts']))
    (node.base/'radio_permits.json').write_text(json.dumps(records,ensure_ascii=False),'utf-8')
    node.emit('radio_permits',target)
    return True

def allowed(node,onion=None):
    onion=onion or node.me.onion
    if node.staff_hidden(onion):return False
    if node.role(onion) in ('admin','mod'):return True
    decision=load(node).get(onion)
    return bool(decision and valid(node,decision) and decision['allow'])

def proof(node,onion=None):
    onion=onion or node.me.onion
    return {'decision':load(node).get(onion),'mods':node._staff_load().get('mods')}

def accept(node,onion,bundle):
    if isinstance(bundle,dict) and len(json.dumps(bundle))<=24000:
        if isinstance(bundle.get('mods'),dict):node._take_modlist(bundle['mods'])
        decision=bundle.get('decision')
        if isinstance(decision,dict):take(node,decision)
    return allowed(node,onion)

async def issue(node,target,on=True):
    from net_core import normalize_code
    target=normalize_code(target)
    if node.my_role() not in ('admin','mod') or not target:return None
    if node.role(target) and node.my_role()!='admin':return None
    decision=node.me.sign_obj({'t':'echoes-radio-permit','by':node.me.onion,'target':target,
        'allow':bool(on),'ts':time.time(),'id':secrets.token_hex(12)})
    if not take(node,decision):return None
    await node._staff_publish(decision,'echoes-radio-permit','radio-permit:'+target)
    packet={'t':'radio_permit','proof':proof(node,target)}
    await node._broadcast_friends(packet)
    peer=node.peers.get(target)
    if peer:await peer.send(packet)
    return decision
