"""Per-page dispatch for source-bound room geometry and measurements."""
from __future__ import annotations

import math
from pathlib import Path

from stoa_vista_specialists.geometry import GeometryTools


class _UnsupportedNative:
    # Generic uploads do not inherit Swiss PDF/DXF verification merely because
    # a PDF happens to contain drawing primitives.
    sources = {}


class Geometry:
    def __init__(self, root, directory, registry, raster_factory=None):
        self.root, self.directory, self.registry = Path(root).resolve(), Path(directory).resolve(), registry
        self.directory.mkdir(parents=True, exist_ok=True)
        self._pages = {}
        for page_id, source in registry.sources.items():
            kwargs = {'native_source':source} if source.get('native_geometry') else {'native_backend':_UnsupportedNative()}
            if raster_factory is not None:kwargs['raster_backend']=raster_factory(self.root,self.directory/page_id/'raster')
            self._pages[page_id] = GeometryTools(self.root, self.directory/page_id, source['source_id'], source['source_sha256'], source['page'], source['width'], source['height'], **kwargs)
        self._evidence_owner = {}

    def _combined(self, field):
        result = {}
        for backend in self._pages.values():
            for ident, item in getattr(backend,field).items():
                if ident in result and result[ident] != item:
                    raise ValueError(field + ' identity collision across pages')
                result[ident] = item
        return result

    @property
    def candidates(self): return self._combined('candidates')

    @property
    def calibrations(self): return self._combined('calibrations')

    def _owner(self, field, ident):
        owners = [page_id for page_id, backend in self._pages.items() if ident in getattr(backend,field)]
        if len(owners) != 1:
            raise ValueError('Unknown or ambiguous ' + field + ' identity')
        return owners[0]

    def native_room_candidates(self, page_id, room_identifier=None, timeout=None):
        backend = self._pages[page_id]
        source = self.registry.sources[page_id]
        self.registry.checked_view(self.registry.initial_for(page_id)['view_id'])
        return backend.native_room_candidates(source['source_id'],source['page'],room_identifier,timeout=timeout)

    def predict_room_polygons(self, view, timeout=120):
        registered = self.registry.checked_view(view['view_id'])
        if registered != view:
            raise ValueError('View does not match registered source')
        return self._pages[view['page_id']].predict_room_polygons(registered,timeout=timeout)

    def register_length_evidence(self, evidence):
        page_id = evidence.get('page_id')
        if page_id not in self._pages:
            raise ValueError('Registered page_id required for length evidence')
        source = self.registry.sources[page_id]
        self.registry.checked_view(self.registry.initial_for(page_id)['view_id'])
        if (evidence.get('source_id'),evidence.get('source_sha256'),evidence.get('page')) != (source['source_id'],source['source_sha256'],source['page']):
            raise ValueError('Evidence source/page mismatch')
        ident = self._pages[page_id].register_length_evidence(evidence)
        if ident in self._evidence_owner and self._evidence_owner[ident] != page_id:
            raise ValueError('Evidence identity collision')
        self._evidence_owner[ident] = page_id
        return ident

    def calibrate(self, evidence_id, p0, p1, length, unit):
        page_id = self._evidence_owner[evidence_id]
        self.registry.checked_view(self.registry.initial_for(page_id)['view_id'])
        return self._pages[page_id].calibrate(evidence_id,p0,p1,length,unit)

    def measure_room(self, candidate_id, calibration_id):
        try:
            page_id = self._owner('candidates',candidate_id)
            if self._owner('calibrations',calibration_id) != page_id:
                raise ValueError('Candidate and calibration belong to different pages')
            self.registry.checked_view(self.registry.initial_for(page_id)['view_id'])
            return self._pages[page_id].measure_room(candidate_id,calibration_id)
        except (KeyError,ValueError,OSError) as exc:
            return {'status':'unavailable','candidate_id':candidate_id,'calibration_id':calibration_id,'area_m2':None,'reason':str(exc)}

    def measure_distance(self, calibration_id, p0, p1):
        try:
            page_id = self._owner('calibrations',calibration_id)
            source = self.registry.sources[page_id]
            self.registry.checked_view(self.registry.initial_for(page_id)['view_id'])
            cal = self._pages[page_id].calibrations[calibration_id]
            if cal.get('calibration_id') != calibration_id or (cal.get('source_id'),cal.get('source_sha256'),cal.get('page')) != (source['source_id'],source['source_sha256'],source['page']):
                raise ValueError('Calibration identity or page mismatch')
            for point in (p0,p1):
                if not isinstance(point,(list,tuple)) or len(point)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in point) or not (0<=point[0]<source['width'] and 0<=point[1]<source['height']):
                    raise ValueError('Invalid page endpoint')
            scale = cal['metres_per_pixel']
            if type(scale) not in (int,float) or not math.isfinite(scale) or scale <= 0:
                raise ValueError('Invalid or uncertain scale')
            distance = math.dist(p0,p1)
            if distance <= 0: raise ValueError('Distinct endpoints required')
            return {'status':'available','page_id':page_id,'calibration_id':calibration_id,'p0':list(p0),'p1':list(p1),'pixel_distance':distance,'distance_m':distance*scale,
                    'estimate':cal.get('kind') not in ('native_verified_pdf_scale_and_dxf_units','native_ifc_units'),
                    'calibration_kind':cal.get('kind'),'source_id':source['source_id'],'source_sha256':source['source_sha256'],'page':source['page']}
        except (KeyError,TypeError,ValueError,OSError) as exc:
            return {'status':'unavailable','calibration_id':calibration_id,'distance_m':None,'reason':str(exc)}

    def close(self):
        errors=[]
        for page_id, backend in self._pages.items():
            try: backend.close()
            except Exception as exc: errors.append(page_id+': '+str(exc))
        if errors: raise RuntimeError('; '.join(errors))
