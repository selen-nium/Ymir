"""VISTA registration and reference-free specialist dispatch."""
import base64
import math
from pathlib import Path
import jsonschema
from PIL import Image, ImageDraw
from shapely.geometry import Polygon
from stoa.vision import Archive, image_content
from .common import CALIBRATE, RASTER, NATIVE, digest, put, sha


def valid_polygon(value):
    exterior, holes = value['exterior'], value.get('holes', [])
    if any(len(p) != 2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in p)
           for ring in [exterior, *holes] for p in ring):
        raise ValueError('Nonfinite polygon coordinates')
    polygon = Polygon(exterior, holes)
    if not polygon.is_valid or polygon.is_empty or polygon.area <= 0:
        raise ValueError('Invalid polygon; repair is outside this pilot')
    return polygon


class SpecialistAdapter:
    def __init__(self, case, condition, folder, backend, root):
        if condition not in ('native','raster'):
            raise ValueError('Unknown specialist condition')
        self.case, self.condition, self.folder = case, condition, Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.backend, self.root = backend, Path(root)
        self.archive = Archive([dict(image=str(self.root/case['image']), width=case['width'], height=case['height'])])
        self.tools = self.archive.tools + ([CALIBRATE, RASTER] if condition=='raster' else [NATIVE])
        self.views, self.candidates, self.calibrations = {}, {}, {}
        self.predicted_views = []
        self._register(self.root/case['image'], 0, 0, case['width'], case['height'])

    def _register(self, path, x, y, width, height):
        with Image.open(path) as image:
            w, h = image.size
        value = dict(path=str(Path(path).resolve()), width=w, height=h,
            source_id=self.case['id'], source_sha256=self.case['image_sha256'],
            original_width=self.case['width'], original_height=self.case['height'],
            transform=dict(scale_x=width/w,scale_y=height/h,origin_x=x,origin_y=y),
            image_sha256=sha(path))
        ident = 'view-'+digest({k:v for k,v in value.items() if k!='path'})[:20]
        value['view_id'] = ident
        self.views[ident] = value
        put(self.folder/'views.json', self.views)
        return value

    def initial_view(self):
        return next(iter(self.views.values()))

    def public_view(self, view):
        return {k:v for k,v in view.items() if k!='path'}

    def _calibrate(self, args):
        factors = dict(m=1,cm=.01,mm=.001,ft=.3048,in_=.0254)
        factors['in'] = factors.pop('in_')
        for point in (args['p0'],args['p1']):
            if len(point)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in point):
                raise ValueError('Invalid endpoints')
            if not (0 <= point[0] < self.case['width'] and 0 <= point[1] < self.case['height']):
                raise ValueError('Calibration endpoint outside original page')
        length = args['length']
        if type(length) not in (int,float) or not math.isfinite(length) or length<=0:
            raise ValueError('Positive finite length required')
        distance = math.dist(args['p0'], args['p1'])
        if distance<2 or not args['evidence'].strip():
            raise ValueError('Distinct endpoints and visible length evidence required')
        value = dict(**args, metres_per_pixel=length*factors[args['unit']]/distance,
                     kind='model_read_raster_length', source_id=self.case['id'])
        value['calibration_id'] = 'cal-'+digest(value)[:20]
        self.calibrations[value['calibration_id']] = value
        put(self.folder/'calibrations.json', self.calibrations)
        return dict(calibration=value), []

    def execute(self, name, args):
        schema = next((t['function']['parameters'] for t in self.tools if t['function']['name']==name), None)
        if schema is None:
            raise ValueError('Tool unavailable in this condition')
        jsonschema.validate(args,schema)
        if name in ('inspect','read_pixels'):
            meta, images = self.archive.execute(name,args)
            if name=='inspect' and meta.get('ok'):
                registered = []
                entries = meta['metadata']['views']
                if len(entries)!=len(images):
                    raise ValueError('VISTA image metadata mismatch')
                for index, (entry, content) in enumerate(zip(entries,images)):
                    path = self.folder/f'inspect-{len(self.views)}-{index}.png'
                    path.write_bytes(base64.b64decode(content['image_url']['url'].split(',',1)[1]))
                    region=entry['region']
                    view=self._register(path,region['x'],region['y'],region['width'],region['height'])
                    if [view['width'],view['height']] != [entry['image_size']['width'],entry['image_size']['height']]:
                        raise ValueError('VISTA dimensions differ from image')
                    registered.append(self.public_view(view))
                meta['registered_views'] = registered
            return meta, images
        if name=='calibrate':
            return self._calibrate(args)
        if name=='predict_room_polygons':
            view = self.views[args['view_id']]
            if len(self.predicted_views)>=2 or args['view_id'] in self.predicted_views:
                raise ValueError('Two predictions maximum; re-query requires another view')
            # Failed requests count; every backend response is retained.
            self.predicted_views.append(args['view_id'])
            result = self.backend.predict(view)
        elif name=='native_room_candidates':
            if args['source_id']!=self.case['id'] or args['page']!=0:
                raise ValueError('Source/page outside this trajectory')
            if args['room_identifier']!=self.case['room_identifier']:
                raise ValueError('Use the public target room identifier')
            result = self.backend.candidates(**args)
        else:
            raise ValueError('Unknown tool')
        serial = len(list(self.folder.glob('prediction-*.json')))
        put(self.folder/f'prediction-{serial}.json', result)
        for candidate in result.get('candidates',[]):
            ident = candidate['candidate_id']
            if ident in self.candidates and self.candidates[ident]!=candidate:
                raise ValueError('Candidate ID collision')
            self.candidates[ident] = candidate
            if self.condition=='native' and candidate.get('calibration'):
                calibration=candidate['calibration']
                self.calibrations[calibration['calibration_id']]=calibration
        put(self.folder/'candidates.json', self.candidates)
        put(self.folder/'calibrations.json', self.calibrations)
        # Always draw on the original page, without altering any polygon.
        paths=[]
        if result.get('candidates'):
            image=Image.open(self.root/self.case['image']).convert('RGB')
            draw=ImageDraw.Draw(image)
            for candidate in result['candidates']:
                ring=candidate['polygon']['exterior']
                points=[tuple(p) for p in ring]
                if not points: continue
                draw.line(points+[points[0]],fill=(230,40,20),width=5)
                for hole in candidate['polygon'].get('holes',[]):
                    hp=[tuple(p) for p in hole]
                    draw.line(hp+[hp[0]],fill=(180,0,180),width=5)
                draw.text(points[0],candidate['candidate_id'],fill=(0,80,180))
            path=self.folder/f'overlay-{serial}.png';image.save(path);paths.append(path)
        return result, [image_content(path,max_side=2000) for path in paths]

    def bind(self, output):
        if output['status']!='answered':
            if any(output[k] is not None for k in ('value','candidate_id','calibration_id')):
                raise ValueError('Abstention requires null measurement fields')
            return None
        if type(output['value']) not in (int,float) or not math.isfinite(output['value']) or output['value']<=0:
            raise ValueError('Positive finite model stated area required')
        candidate=self.candidates[output['candidate_id']]
        calibration=self.calibrations[output['calibration_id']]
        polygon=valid_polygon(candidate['polygon'])
        flags=candidate.get('quality_flags',[])
        if flags:
            raise ValueError('Candidate quality uncertainty requires abstention: '+str(flags))
        if self.condition=='native' and candidate['calibration']['calibration_id']!=output['calibration_id']:
            raise ValueError('Native candidate requires its own verified calibration')
        if self.condition=='raster' and calibration.get('kind')!='model_read_raster_length':
            raise ValueError('Native calibration cannot enter raster trajectories')
        for x,y in polygon.exterior.coords:
            if not(0<=x<=self.case['width'] and 0<=y<=self.case['height']):
                raise ValueError('Candidate outside original page')
        mpp=calibration['metres_per_pixel']
        if type(mpp) not in (int,float) or not math.isfinite(mpp) or mpp<=0:
            raise ValueError('Invalid calibration scale')
        area=polygon.area*mpp*mpp
        return dict(candidate_id=output['candidate_id'],calibration_id=output['calibration_id'],
            canonical_area_m2=area,model_stated_area_m2=output['value'],
            numeric_conflict=not math.isclose(area,output['value'],rel_tol=.001,abs_tol=.001),
            pixel_area=polygon.area,calibration=calibration,candidate=candidate,
            boundary_evidence_status='solver_selected_overlay; no independent semantic certification')
