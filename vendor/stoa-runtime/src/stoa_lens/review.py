"""Bounded source-pixel context for specialist proposals, with registered transforms."""
import math
from pathlib import Path

from PIL import Image
from stoa.vision import image_content,json_content


def proposal_contexts(registry,proposals,directory,maximum=3):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    metadata,images=[],[]
    for proposal in proposals[:maximum]:
        if registry.inspection_views>=9:break
        source=registry.sources[proposal['page_id']]
        registry.checked_view(registry.initial_for(proposal['page_id'])['view_id'])
        x0,y0,x1,y1=proposal['native_box_xyxy']
        pad=max(48.,1.25*max(x1-x0,y1-y0))
        region=[max(0,math.floor(x0-pad)),max(0,math.floor(y0-pad)),
                min(source['width'],math.ceil(x1+pad)),min(source['height'],math.ceil(y1+pad))]
        left,top,right,bottom=region
        if left>=right or top>=bottom:raise ValueError('Invalid proposal context region')
        path=directory/(proposal['id']+'-context.png')
        with Image.open(source['path']) as original:
            crop=original.convert('RGB').crop(tuple(region))
            scale=1600/max(crop.size)
            crop=crop.resize((max(1,round(crop.width*scale)),max(1,round(crop.height*scale))),Image.Resampling.NEAREST)
            crop.save(path)
        view=registry._register(source,path,left,top,right-left,bottom-top)
        record={k:v for k,v in view.items() if k not in ('path','pdf_path')}
        record.update(proposal_ids=[proposal['id']],native_page_region_xyxy=region,
                      purpose='Source-pixel proposal context. Check identity and duplicates; this is not a new detector proposal.')
        registry.inspection_views+=1
        metadata.append(record)
        images.extend([json_content({'proposal_context':record}),image_content(path)])
    return metadata,images
