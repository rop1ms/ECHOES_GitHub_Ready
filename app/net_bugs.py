"""Private bug reports to verified staff, with durable retries and acknowledgments.

This protocol deliberately works before friendship. It grants no chat, file,
moderation, or profile access to a stranger and never broadcasts reports.
"""
from __future__ import annotations
import json
import math
import re
import secrets
import time

MAX_PENDING = 30


def recipients(node):
    ids = [node.admin_onion] + list(node.mods())
    return list(dict.fromkeys(peer for peer in ids if peer and peer != node.me.onion and node.role(peer) in ('admin','mod')))


def clean(payload):
    if not isinstance(payload,dict) or not re.fullmatch('[0-9a-f]{24}',str(payload.get('id') or '')): return None
    result = {'id':payload['id']}
    category=payload.get('category','bug')
    if category not in ('bug','radio','appeal','other'):return None
    result['category']=category
    for key, limit, minimum in [('title',120,4),('details',3000,15),('steps',1500,0),('version',40,0),('name',40,0)]:
        value = payload.get(key,'')
        if not isinstance(value,str) or not minimum <= len(value.strip()) <= limit:return None
        result[key]=value.strip()
    diag = payload.get('diag')                     # техническая сводка о ПК (diagnostics.py), необязательно
    if isinstance(diag,str) and diag.strip():result['diag']=diag.strip()[:4000]
    ts = payload.get('ts')
    if isinstance(ts,bool) or not isinstance(ts,(int,float)) or not math.isfinite(ts):return None
    result['ts']=max(0.,min(float(ts),time.time()+60))
    return result


def queue(node, peer, title, details, steps='', version='', category='bug', diag=''):
    if peer not in recipients(node):raise ValueError('invalid_recipient')
    payload=clean({'id':secrets.token_hex(12),'title':title,'details':details,'steps':steps,
                   'version':version,'name':node._name(),'ts':time.time(),'category':category,'diag':diag})
    if payload is None:raise ValueError('invalid_report')
    if len(node.store.q("SELECT id FROM bug_reports WHERE direction='out' AND state='queued' LIMIT 30"))>=MAX_PENDING:
        raise ValueError('queue_full')
    node.store.x("INSERT INTO bug_reports(id,peer,direction,ts,payload,state) VALUES(?,?,'out',?,?,'queued')",
                 (payload['id'],peer,payload['ts'],json.dumps(payload,ensure_ascii=False)))
    node.emit('bug_reports')
    node.poke(peer)
    # Network scheduling happens on the node's loop, after the durable local write.
    loop=getattr(node,'loop',None)
    if loop is not None and not loop.is_closed():node.call(deliver,node,peer)
    return payload['id']


def pending_peers(node):
    outgoing=[row['peer'] for row in node.store.q("SELECT DISTINCT peer FROM bug_reports WHERE direction='out' AND state='queued' LIMIT 30")
              if node.role(row['peer']) in ('admin','mod')]
    answers=[row['peer'] for row in node.store.q("SELECT DISTINCT peer FROM bug_reports WHERE direction='in' AND state='answer_queued' LIMIT 30")] if node.my_role() in ('admin','mod') else []
    return list(dict.fromkeys(outgoing+answers))


async def deliver(node, peer):
    connection=node.peers.get(peer)
    if connection is None:
        nostr=getattr(node,'nostr',None)
        if nostr is not None and getattr(nostr,'up_count',0):
            public_key=node._npk_for(peer)
            if public_key:connection=node._nostr_attach(peer,public_key)
    if connection is not None:await flush(node,connection)
    else:node.poke(peer)


async def flush(node, connection):
    if node.my_role() in ('admin','mod'):
        for row in node.store.q("SELECT id,payload FROM bug_reports WHERE peer=? AND direction='in' AND state='answer_queued' LIMIT 30",(connection.onion,)):
            payload=json.loads(row['payload'])
            await connection.send({'t':'staff_reply','id':row['id'],'answer':payload['answer']})
    if node.role(connection.onion) not in ('admin','mod'):return
    # No status is changed until the authenticated recipient acknowledges.
    for row in node.store.q("SELECT payload FROM bug_reports WHERE peer=? AND direction='out' AND state='queued' ORDER BY ts LIMIT 30",(connection.onion,)):
        payload=clean(json.loads(row['payload']))
        if payload:await connection.send({'t':'bug_report','report':payload})


async def receive(node, connection, message):
    peer=connection.onion
    if message.get('t')=='staff_reply_ack':
        if node.my_role() in ('admin','mod'):
            node.store.x("UPDATE bug_reports SET state='answered' WHERE id=? AND peer=? AND direction='in' AND state='answer_queued'",(str(message.get('id',''))[:24],peer))
            node.emit('bug_reports')
        return
    if message.get('t')=='staff_reply':
        if node.role(peer) not in ('admin','mod'):return
        answer=message.get('answer');ident=str(message.get('id',''))[:24]
        if not isinstance(answer,dict) or answer.get('state') not in ('approved','declined','replied') or not isinstance(answer.get('text'),str) or len(answer['text'])>1500:return
        rows=node.store.q("SELECT payload FROM bug_reports WHERE id=? AND peer=? AND direction='out'",(ident,peer))
        if not rows:return
        payload=json.loads(rows[0]['payload'])
        if isinstance(answer.get('permit'),dict):
            from net_radio_auth import accept
            accept(node,node.me.onion,answer['permit'])
        if payload.get('category')=='radio' and answer['state']=='approved':
            from net_radio_auth import allowed
            if not allowed(node):return
        payload['answer']={'text':answer['text'],'state':answer['state'],'by':peer}
        node.store.x("UPDATE bug_reports SET payload=?,state=? WHERE id=? AND direction='out'",(json.dumps(payload,ensure_ascii=False),answer['state'],ident))
        await connection.send({'t':'staff_reply_ack','id':ident});node.emit('bug_reports');return
    if message.get('t')=='bug_ack':
        if node.role(peer) not in ('admin','mod'):return
        ident=message.get('id')
        if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{24}',ident):return
        node.store.x("UPDATE bug_reports SET state='received' WHERE id=? AND peer=? AND direction='out' AND state='queued'",(ident,peer))
        node.emit('bug_reports');return
    if message.get('t')!='bug_report' or node.my_role() not in ('admin','mod'):return
    payload=clean(message.get('report'))
    if payload is None:return
    existing=node.store.q('SELECT peer,direction FROM bug_reports WHERE id=?',(payload['id'],))
    if existing:
        if existing[0]['peer']==peer and existing[0]['direction']=='in':await connection.send({'t':'bug_ack','id':payload['id']})
        return
    now=time.time()
    recent=node.store.q("SELECT id FROM bug_reports WHERE peer=? AND direction='in' AND ts>? LIMIT 5",(peer,now-3600))
    if len(recent)>=5:return
    node.store.x("INSERT INTO bug_reports(id,peer,direction,ts,payload,state) VALUES(?,?,'in',?,?,'new')",
                 (payload['id'],peer,now,json.dumps(payload,ensure_ascii=False)))
    # Keep the inbox bounded, retaining the newest reports.
    node.store.x("DELETE FROM bug_reports WHERE direction='in' AND id NOT IN (SELECT id FROM bug_reports WHERE direction='in' ORDER BY ts DESC LIMIT 1000)")
    await connection.send({'t':'bug_ack','id':payload['id']})
    node.emit('bug_reports')
    node.emit('notify',{'kind':'info','title':'Requests and bugs','text':payload['title'],'tab':'requests'})

async def answer(node,ident,text,state='replied'):
    if node.my_role() not in ('admin','mod') or state not in ('approved','declined','replied'):return False
    rows=node.store.q("SELECT * FROM bug_reports WHERE id=? AND direction='in'",(ident,))
    if not rows:return False
    row=rows[0];payload=json.loads(row['payload'])
    reply={'text':str(text)[:1500],'state':state}
    if payload.get('category')=='radio' and state in ('approved','declined'):
        from net_radio_auth import issue,proof
        if not await issue(node,row['peer'],state=='approved'):return False
        reply['permit']=proof(node,row['peer'])
    payload['answer']=reply
    node.store.x("UPDATE bug_reports SET payload=?,state='answer_queued' WHERE id=? AND direction='in'",(json.dumps(payload,ensure_ascii=False),ident))
    node.emit('bug_reports');await deliver(node,row['peer']);return True
