"""Browser QA fixture. Never imported by the local production launch script."""
import time
from ymir.app import app, jobs
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:3011','http://127.0.0.1:3013'],allow_methods=['*'],allow_headers=['*'])
from stoa_lens.registry import Registry
from stoa_lens.service import Cancelled


def fixture_runner(pages,question,directory,**hooks):
    registry=Registry(pages,directory/'views');pid=registry.initial['page_id']
    def emit(operation,phase,**data):hooks['progress']({'operation':operation,'phase':phase,**data})
    for i in range(3):
        emit('response','started',response_index=i);time.sleep(.3)
        if hooks['cancelled']():raise Cancelled()
        emit('response','completed',response_index=i)
        if i==0:
            for j in range(2):
                emit('tool','started',response_index=i,call_id=f'inspect-{j}',tool='inspect');time.sleep(.3)
                emit('tool','completed',response_index=i,call_id=f'inspect-{j}',tool='inspect',result={'ok':True})
        else:
            routing={'backend':'dfine' if i==1 else 'kimi','requested_class':'door' if i==1 else 'folding door'}
            model='d-fine-l-junction-ema' if i==1 else 'kimi-k3-1-pro'
            detail={'response_index':i,'call_id':f'detect-{i}','tool':'detect_objects','routing':routing,'model':model}
            emit('tool','started',**detail);time.sleep(2)
            if hooks['cancelled']():raise Cancelled()
            emit('tool','completed',**detail,result={'status':'partial','routing':routing,'model':model})
    if 'window' in question.lower():
        return {'status':'needs_review','counts':[{'page_id':pid,'object_class':'window','count':None}],
          'proposals':[{'id':f'window-{i}','page_id':pid,'query_class':'window','native_box_xyxy':box,'confidence':None} for i,box in enumerate([[56,21,184,40],[647,251,667,372],[19,277,37,310]])],
          'accepted_proposal_ids':['window-0','window-1','window-2'],'rooms':[],'text_evidence':[],'measurements':[],'readings':[],'notes':[]}
    return {'status':'needs_review','counts':[{'page_id':pid,'object_class':'door','count':None}],
      'proposals':[{'id':'proposal-1','page_id':pid,'query_class':'door','native_box_xyxy':[40,40,100,100],'confidence':None}],
      'accepted_proposal_ids':['proposal-1'],'rooms':[], 'text_evidence':[], 'measurements':[], 'readings':[], 'notes':['Browser fixture: incomplete source evidence.']}

jobs.runner=fixture_runner
