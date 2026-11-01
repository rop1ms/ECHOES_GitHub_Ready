"""osu!-style numeric search filters, with a plain text remainder."""
import re
OPS={'<':lambda a,b:a<b,'>':lambda a,b:a>b,'<=':lambda a,b:a<=b,'>=':lambda a,b:a>=b,'=':lambda a,b:abs(a-b)<.005}
PATTERN=re.compile(r'\b(bpm|length|stars|ar|cs|od|hp)\s*(<=|>=|=|<|>)\s*(\d+(?:\.\d+)?)',re.I)
def parse(query):
    filters=[(m[1].lower(),m[2],float(m[3])) for m in PATTERN.finditer(query)]
    return PATTERN.sub('',query).strip(),filters
def matches(track,filters,diffs=None):
    for diff in diffs or [{}]:
        values=dict(track);values.update(diff)
        if diff and 'length' in diff:values['length']=float(diff['length'])/1000.
        aliases={'length':('length','duration'),'stars':('stars','sr','star_rating'),'od':('od','overall_difficulty')}
        passed=True
        for key,op,num in filters:
            value=next((values[k] for k in aliases.get(key,(key,)) if values.get(k) is not None),None)
            try:passed=passed and value is not None and OPS[op](float(value),num)
            except (ValueError,TypeError):passed=False
        if passed:return True
    return False
