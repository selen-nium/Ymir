"""One crop, one specialist, host-validated native-page proposals."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import tempfile
import threading
import time
from PIL import Image,ImageDraw
from stoa_vista_specialists.specialists.detectors import map_proposals,SCHEMA
from .routing import ClassRouter

class DetectionError(RuntimeError):
    def __init__(self,message,receipt):super().__init__(message);self.receipt=receipt

class DetectionService:
    def __init__(self,dfine,kimi,directory):
        self.dfine=dfine;self.kimi=kimi;self.directory=Path(directory);self.router=ClassRouter();self.lock=threading.Lock();self.sequence=0
    def close(self):
        for adapter in (self.dfine,self.kimi):
            if callable(getattr(adapter,'close',None)):adapter.close()
    def detect(self,view,region,object_class,reason,timeout):
        import jsonschema
        start=time.monotonic()
        if not isinstance(reason,str) or not reason.strip():raise ValueError('A reason is required')
        region_help=('region must be [left, top, right, bottom] in integer VIEW pixels, '
                     'not [x, y, width, height]. Convert the latter to [x, y, x+width, y+height].')
        if not isinstance(region,(list,tuple)) or len(region)!=4 or any(type(x) is not int for x in region):
            raise ValueError(region_help)
        left,top,right,bottom=region
        if not (0<=left<right<=view['width'] and 0<=top<bottom<=view['height']):
            raise ValueError(region_help+' Require 0 <= left < right <= '+str(view['width'])+
                             ' and 0 <= top < bottom <= '+str(view['height'])+'; received '+str(list(region)))
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<=0:raise ValueError('Positive finite timeout required')
        route=self.router.route(object_class);adapter=self.dfine if route['backend']=='dfine' else self.kimi
        # Validate geometry before paying or invoking any specialist.
        map_proposals(view,region,[],adapter.model,'uncalibrated')
        with self.lock:
            self.sequence+=1;ident=self.sequence
        folder=self.directory/str(ident);folder.mkdir(parents=True,exist_ok=False)
        try:
            with Image.open(view['path']) as im:
                if im.size!=(view['width'],view['height']):raise ValueError('View size mismatch')
                crop=im.convert('RGB').crop(tuple(region));path=folder/'crop.png';crop.save(path)
            crop_sha=hashlib.sha256(path.read_bytes()).hexdigest();remaining=timeout-(time.monotonic()-start)
            if remaining<=0:raise TimeoutError('Deadline before detector invocation')
            if route['backend']=='dfine':
                value,receipt=adapter.predict(path,route['canonical_class'],remaining)
            else:
                prompt=('Requested object class: '+object_class+'. Only label instances that match this exact object class; '
                        'use the requested class as the label. '+reason)
                value,receipt=adapter.predict(path,prompt,remaining)
            jsonschema.validate(value,SCHEMA)
            selected=[]; rejected=[]
            for index,row in enumerate(value['instances']):
                if self.router.accepts_label(object_class,row['label']):selected.append((index,row))
                else:rejected.append({'raw_index':index,'instance':row,'reason':'label does not match requested class'})
            if route['backend']=='dfine' and rejected:raise ValueError('D-FINE returned a different class than requested')
            # Map all before filtering so stable IDs retain raw order and invalid geometry cannot hide.
            normalized=route['backend']=='kimi'
            confidence='vlm_self_reported_uncalibrated' if normalized else 'detector_score_uncalibrated'
            all_mapped=map_proposals(view,region,value['instances'],adapter.model,confidence,normalized=normalized)
            proposals=[all_mapped[index] for index,_ in selected]
            with Image.open(path) as im:
                overlay=im.convert('RGB');draw=ImageDraw.Draw(overlay)
                for p in proposals:
                    b=p['view_box_xyxy'];local=[b[0]-region[0],b[1]-region[1],b[2]-region[0],b[3]-region[1]]
                    draw.rectangle(local,outline='red',width=2);draw.text((local[0],local[1]),p['label'],fill='red')
                overlay.save(folder/'overlay.png')
            record={'model':adapter.model,'status':value['status'],'uncertainties':value['uncertainties'],
                'proposals':proposals,'routing':route,'receipt':receipt,'raw':value,'rejected':rejected,
                'crop_sha256':crop_sha,'region':list(region)}
            (folder/'receipt.json').write_text(json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
            return record
        except BaseException as error:
            data={'routing':route,'error_type':type(error).__name__,'error':str(error),
                  'receipt':getattr(error,'receipt',None)}
            (folder/'failure.json').write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+'\n')
            raise DetectionError('Specialist failed: '+str(error),data) from error
