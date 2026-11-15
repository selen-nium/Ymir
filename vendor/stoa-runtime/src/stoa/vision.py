"""Official VISTA inspection operations with a local static drawing archive."""
import base64
import io
import json
import sys
from pathlib import Path

from PIL import Image

from .config import ROOT

VISTA_SOURCE = Path(__file__).resolve().parents[1]
if str(VISTA_SOURCE) not in sys.path:
    sys.path.insert(0,str(VISTA_SOURCE))

def image_content(path, max_side=1600):
    with Image.open(path) as source:
        im=source.convert('RGB')
        if max_side: im.thumbnail((max_side,max_side),Image.Resampling.LANCZOS)
        out=io.BytesIO();im.save(out,format='PNG')
    return {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()}}

def json_content(data):
    return {'type':'text','text':json.dumps(data,ensure_ascii=False)}

class Archive:
    def __init__(self,pages):
        # Load actual upstream implementation, not a prompt merely labelled VISTA.
        from vista.core.session import SessionController
        from vista.core.tools import build_inspect_tool, build_read_pixels_tool

        class Controller(SessionController):
            observation_mode='vision'
            display_size=1600
            def __init__(self,paths):
                self.paths=paths
                self._current_size=(1600,1600)
                self._pixel_readout_symbol_by_color={}
            def visual_size(self): return self._current_size
            def _turn_frame_paths(self,turn):
                return [self.paths[turn]] if 0 <= turn < len(self.paths) else []
            @staticmethod
            def _frame_number(path): return 0
            def _inspection_view(self,selection,index):
                turn=selection.get('turn') if isinstance(selection,dict) else None
                if type(turn) is int and 0 <= turn < len(self.paths):
                    with Image.open(self.paths[turn]) as im: self._current_size=im.size
                return super()._inspection_view(selection,index)
            def _pixel_readout_view(self,selection,index):
                turn=selection.get('turn') if isinstance(selection,dict) else None
                if type(turn) is int and 0 <= turn < len(self.paths):
                    with Image.open(self.paths[turn]) as im: self._current_size=im.size
                return super()._pixel_readout_view(selection,index)

        self.pages=pages
        self.controller=Controller([ROOT/p['image'] for p in pages])
        size=max(max(p['width'],p['height']) for p in pages)
        schemas=[build_inspect_tool(size),build_read_pixels_tool(size)]
        # Tighten only batch limits for this small-budget application.
        schemas[0]['inputSchema']['properties']['views']['maxItems']=3
        schemas[1]['inputSchema']['properties']['views']['maxItems']=2
        self.tools=[{'type':'function','function':{'name':s['name'],'description':s['description'], 'parameters':s['inputSchema']}} for s in schemas]

    def execute(self,name,arguments):
        if name not in {'inspect','read_pixels'}:
            return {'ok':False,'metadata':{'error':'Unknown tool'}},[]
        views=arguments.get('views',[]) if isinstance(arguments,dict) else []
        if len(views) > (3 if name=='inspect' else 2):
            return {'ok':False,'metadata':{'error':'Too many views in this pilot'}},[]
        result=getattr(self.controller,'_'+name)(arguments)
        images=result.pop('images',[])
        content=[]
        for im in images:
            content.append({'type':'image_url','image_url':{'url':f"data:{im['mime_type']};base64,{im['data']}"}})
        return result,content

def overview(pages,max_side=1600):
    result=[]
    for n,p in enumerate(pages):
        result += [json_content({'page':n,'vista_turn':n,'original_pixels':[p['width'],p['height']],
                  'evidence_coordinates':'0..1000 relative to the ORIGINAL full page, not a crop'}), image_content(ROOT/p['image'],max_side=max_side)]
    return result

def tiles(pages):
    result=overview(pages)
    for n,p in enumerate(pages):
        with Image.open(ROOT/p['image']) as im:
            w,h=im.size
            for i,(x0,y0,x1,y1) in enumerate([(0,0,.55,.55),(.45,0,1,.55),(0,.45,.55,1),(.45,.45,1,1)]):
                crop=im.crop((int(x0*w),int(y0*h),int(x1*w),int(y1*h))).convert('RGB')
                crop.thumbnail((1600,1600),Image.Resampling.LANCZOS)
                out=io.BytesIO();crop.save(out,format='PNG')
                result += [json_content({'page':n,'tile':i,'full_page_bbox':[int(v*1000) for v in (x0,y0,x1,y1)]}),
                           {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()}}]
    return result
