"""Analytic checks for source-bound host measurement identifiers."""
import copy

import pytest

from stoa_lens.geometry import Geometry
from stoa_vista_specialists.geometry import GeometryTools


class NoRaster:
    def close(self):
        pass


class NoNative:
    sources = {}


def test_room_receipt_preserves_holes_and_source(tmp_path):
    geometry = GeometryTools(tmp_path, tmp_path / 'run', 'source', 'hash', 0, 100, 100,
                             raster_backend=NoRaster(), native_backend=NoNative())
    calibration = {'calibration_id': 'cal', 'source_id': 'source', 'source_sha256': 'hash',
                   'page': 0, 'metres_per_pixel': .1}
    candidate = {'candidate_id': 'room', 'polygon': {'exterior': [[0, 0], [100, 0], [100, 100], [0, 100]],
                 'holes': [[[20, 20], [40, 20], [40, 40], [20, 40]]]},
                 'quality_flags': [], 'calibration': calibration,
                 'provenance': {'source_id': 'source', 'source_sha256': 'hash', 'page': 0, 'backend': 'native'}}
    geometry._register_candidates({'candidates': [candidate]})
    receipt = geometry.measure_room('room', 'cal')
    assert receipt['area_m2'] == pytest.approx(96)
    assert receipt['source_sha256'] == 'hash'
    assert receipt['method'] == 'polygon_area_minus_holes'
    assert receipt['measurement_id'] == geometry.measure_room('room', 'cal')['measurement_id']
    changed = copy.deepcopy(geometry.calibrations['cal'])
    changed['metres_per_pixel'] = .2
    geometry.calibrations['cal'] = changed
    assert receipt['measurement_id'] != geometry.measure_room('room', 'cal')['measurement_id']
    assert geometry.measure_room('room', 'unknown')['status'] == 'unavailable'


def test_distance_receipt_binds_source_and_endpoints(tmp_path):
    class Registry:
        sources = {'page': {'source_id': 'source', 'source_sha256': 'hash', 'page': 0,
                            'width': 100, 'height': 100}}

        def initial_for(self, page_id):
            return {'view_id': page_id}

        def checked_view(self, view_id):
            return {'view_id': view_id}

    geometry = Geometry(tmp_path, tmp_path / 'run', Registry(), raster_factory=lambda *_: NoRaster())
    geometry._pages['page'].calibrations['cal'] = {'calibration_id': 'cal', 'source_id': 'source',
        'source_sha256': 'hash', 'page': 0, 'metres_per_pixel': .1, 'kind': 'native_ifc_units'}
    result = geometry.measure_distance('cal', [0, 0], [30, 40])
    assert result['distance_m'] == pytest.approx(5)
    assert result['unit'] == 'm' and not result['estimate']
    assert result['measurement_id'] == geometry.measure_distance('cal', [0, 0], [30, 40])['measurement_id']
    assert result['measurement_id'] != geometry.measure_distance('cal', [0, 0], [30, 41])['measurement_id']
    assert geometry.measure_distance('cal', [0, 0], [100, 0])['status'] == 'unavailable'


def test_final_answer_selects_only_recorded_available_measurements():
    from types import SimpleNamespace
    from stoa_lens.harness import LensHarness
    harness=LensHarness.__new__(LensHarness)
    harness.agent_profile=None
    harness.proposals={};harness.detections=[];harness.text_evidence={}
    harness.registry=SimpleNamespace(sources={},views={})
    harness.geometry=SimpleNamespace(candidates={})
    harness.inventory=lambda: {}
    harness.measurements=[{'tool':'measure_distance','arguments':{},'result':{
        'status':'available','measurement_id':'selected','distance_m':5}},
        {'tool':'measure_distance','arguments':{},'result':{
        'status':'available','measurement_id':'exploratory','distance_m':20}}]
    final={'status':'answered','accepted_proposal_ids':[], 'count_requests':[], 'rooms':[],
           'measurement_ids':['selected']}
    bound=harness.bind(final)
    assert bound['measurement_ids']==['selected']
    assert len(bound['measurements'])==1 and bound['measurements'][0]['result']['distance_m']==5
    bound['measurements'][0]['result']['distance_m']=100
    assert harness.measurements[0]['result']['distance_m']==5
    with pytest.raises(ValueError,match='Unknown or unavailable'):
        harness.bind(dict(final,measurement_ids=['fabricated']))
    with pytest.raises(ValueError,match='recorded evidence'):
        harness.bind(dict(final,measurement_ids=[]))
