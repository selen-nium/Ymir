"""Extract one complete JSON object without guessing or repairing its contents."""
import json
import re


def parse_object(text):
    if not isinstance(text,str):raise ValueError('Expected JSON response text')
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError('Duplicate JSON key: '+key)
            value[key]=item
        return value
    def invalid(value):raise ValueError('Nonfinite JSON constant: '+value)
    decoder=json.JSONDecoder(object_pairs_hook=unique,parse_constant=invalid)
    stripped=text.strip()
    try:
        value=decoder.decode(stripped)
        if not isinstance(value,dict):raise ValueError('Expected one JSON object')
        return value
    except json.JSONDecodeError:pass
    # A single fenced object is accepted; multiple candidate answers are ambiguous.
    fences=re.findall(r'```(?:json)?\s*([\s\S]*?)```',text,flags=re.I)
    candidates=[]
    for block in fences:
        value=decoder.decode(block.strip())
        if not isinstance(value,dict):raise ValueError('Expected JSON object in fence')
        candidates.append(value)
    if len(candidates)==1:
        # Reject another JSON object outside the fence rather than choosing one.
        rest=re.sub(r'```(?:json)?\s*[\s\S]*?```','',text,flags=re.I)
        for match in re.finditer(r'\{',rest):
            try:other,_=decoder.raw_decode(rest[match.start():])
            except (ValueError,json.JSONDecodeError):continue
            if isinstance(other,dict):raise ValueError('Multiple JSON response objects')
        return candidates[0]
    raise ValueError('Expected a single complete JSON object')
