"""Present host-bound Lens evidence without manufacturing confidence or geometry."""
from __future__ import annotations


def count_noun(cls, n):
    singular = {'toilet_bowl': 'toilet', 'wash_basin': 'washbasin'}.get(cls, cls.replace('_', ' '))
    return singular if n == 1 else singular + ('es' if singular.endswith(('ch', 'sh', 'x')) else 's')


def count_group_noun(group, n):
    classes = group.get('object_classes', [])
    if len(classes) > 1:
        joiner = ' or ' if n == 1 else ' and '
        return joiner.join(count_noun(cls, n) for cls in classes) + ('' if n == 1 else ' in total')
    return count_noun(group['object_class'], n)


def bounds(points):
    return [min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)] if points else None


def answer_for(job_id, result, page_sheets):
    citations=[];by_id={};reviews=[]
    def cite(ident,page,label,**geometry):
        if ident in by_id:return by_id[ident]
        n=len(citations)+1;by_id[ident]=n
        citations.append({'n':n,'label':label,'sheet':page_sheets.get(page),'page_id':page,'evidence_id':ident,'provenance':'source_bound',**geometry})
        return n
    accepted=set(result.get('accepted_proposal_ids',[]))
    for p in result.get('proposals',[]):
        if p['id'] in accepted:
            cite(p['id'],p['page_id'],p['query_class'].replace('_',' ')+' · '+p.get('confidence_kind','specialist proposal'),box=p['native_box_xyxy'],score=p.get('confidence'),object_class=p['query_class'],proposal_id=p['id'],provenance_detail=p.get('provenance'))
    cited_text={ident for reading in result.get('readings',[]) for ident in reading['evidence_ids']}
    cited_text.update(ident for room in result.get('rooms',[]) for ident in room.get('text_ids',[]))
    cited_text.update(c.get('evidence',{}).get('upstream_evidence_id') for c in result.get('calibrations',[]))
    for t in result.get('text_evidence',[]):
        if t['id'] not in cited_text:continue
        cite(t['id'],t['page_id'],'Native PDF text · '+t.get('text',''),box=t.get('native_box_xyxy') or t.get('box'),score=None)
    for r in result.get('rooms',[]):
        cite(r['candidate_id'],r['page_id'],r.get('label') or r['room_id'],box=bounds(r['polygon']),polygon=r['polygon'],holes=r.get('holes',[]),score=None,provenance_detail=r.get('geometry_provenance'))
    for i,entry in enumerate(result.get('measurements',[])):
        m=entry.get('result',entry)
        if m.get('p0') and m.get('p1'):
            cite('measurement-'+str(i),m.get('page_id'),'Source-calibrated distance',box=bounds([m['p0'],m['p1']]),endpoints=[m['p0'],m['p1']],score=None)
    for cal in result.get('calibrations',[]):
        if cal.get('p0') and cal.get('p1'):
            page=cal.get('evidence',{}).get('page_id')
            if page in page_sheets:
                cite(cal['calibration_id'],page,f"Calibration · {cal['length']} {cal['unit']}",box=bounds([cal['p0'],cal['p1']]),endpoints=[cal['p0'],cal['p1']],score=None)
    # Registered view readings can be focused even when native PDF text is unavailable.
    for reading in result.get('readings',[]):
        for ident in reading['evidence_ids']:
            if ident in by_id:continue
            view=result.get('evidence_views',{}).get(ident)
            if view:
                tr=view['transform'];x,y=tr['origin_x'],tr['origin_y']
                cite(ident,view['page_id'],'Model reading · '+reading['description'],box=[x,y,x+view['width']*tr['scale_x'],y+view['height']*tr['scale_y']],score=None)
    groups=list(result.get('counts',[]))
    keys={(c['page_id'],c['object_class']) for c in groups}
    for p in result.get('proposals',[]):
        key=(p['page_id'],p['query_class'])
        if key not in keys:
            groups.append({'page_id':key[0],'object_class':key[1],'count':None});keys.add(key)
    for c in groups:
        proposals=[p for p in result.get('proposals',[]) if p['page_id']==c['page_id'] and p['query_class']==c['object_class']]
        reviews.append({'sheet':page_sheets[c['page_id']],'page_id':c['page_id'],'class':c['object_class'],'threshold':0,
                        'boxes':[{'id':p['id'],'box':p['native_box_xyxy'],'score':p.get('confidence'), 'confidence_kind':p.get('confidence_kind'), 'model':p.get('model'), 'accepted':p['id'] in accepted} for p in proposals], 'count':c['count']})
    reasons=result.get('validation_errors',[])+result.get('notes',[])
    if result.get('unavailable_reason'):reasons.append(result['unavailable_reason'])
    counts=[dict(c,sheet=page_sheets[c['page_id']],provenance='host_derived') for c in result.get('counts',[])]
    rooms=[dict(r,sheet=page_sheets[r['page_id']],provenance='host_derived_geometry') for r in result.get('rooms',[])]
    text='Some details need checking.' if result['status']=='needs_review' else 'Here’s what I found on the selected sheets.'
    if len(counts)==1:
        c=counts[0];noun=count_group_noun(c,c['count']);text=f"I found {c['count']} {noun}." if c['count'] is not None else f"I couldn’t confirm the {count_group_noun(c,1)} count. Please check the boxes."
    value=counts[0]['count'] if len(counts)==1 else None
    auto=next((r for r in reviews if r['boxes']),None) if result['status']=='needs_review' else None
    return {'status':result['status'],'text':text,'value':value,'unit':'count' if counts else None,'job_id':job_id,'confidence':None,
            'citations':citations,'counts':counts,'rooms':rooms,'readings':result.get('readings',[]), 'measurements':result.get('measurements',[]),
            'calibrations':result.get('calibrations',[]),'reasons':reasons,'coverage':result.get('coverage'),'reviews':reviews,**({'review':auto} if auto else {})}
