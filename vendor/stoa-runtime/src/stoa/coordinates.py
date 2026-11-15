"""Map model citations from a named inspected view to the immutable original page."""
import math

def map_evidence(evidence,views):
    output=[]
    for item in evidence or []:
        e=dict(item)
        view=views.get(e.get('view_id'))
        box=e.get('bbox')
        if view is None or not isinstance(box,list) or len(box)!=4 or not all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1000 for v in box) or box[2]<=box[0] or box[3]<=box[1]:
            e.update(bbox=[],mapping_error='Invalid view or local evidence box')
        else:
            x,y,w,h=view['region'];pw,ph=view['page_size']
            e.update(page=view['page'],view_bbox=box,bbox=[(x+box[0]/1000*w)/pw*1000,(y+box[1]/1000*h)/ph*1000,(x+box[2]/1000*w)/pw*1000,(y+box[3]/1000*h)/ph*1000],mapped_by_host=True)
        output.append(e)
    return output
