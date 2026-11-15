"""DXF analytic fixtures go through maintained extraction and intake paths."""
import io
import math

import ezdxf
import pytest

from stoa_native_geometry.backend import RegisteredNativeBackend, extract, prepare
from ymir.documents import DocumentStore, SheetSelection
from ymir.drawings import DrawingStore
from stoa_lens.geometry import Geometry
from stoa_lens.registry import Registry


def source(tmp_path, unit=6, side=10):
    doc = ezdxf.new(); doc.units = unit
    entity = doc.modelspace().add_lwpolyline([(0,0),(side,0),(side,side),(0,side)], close=True)
    path = tmp_path / 'source.dxf'; doc.saveas(path)
    return path, entity.dxf.handle


@pytest.mark.parametrize('unit,side', [(6,10), (4,10000), (2,10/.3048)])
def test_dxf_units_and_registered_area(tmp_path, unit, side):
    path, handle = source(tmp_path, unit, side)
    result = prepare(path, tmp_path/'native', options={'element_ids':[handle]})
    assert result['extraction']['rooms'][0]['area_m2'] == pytest.approx(100)
    s = result['source']; candidate = RegisteredNativeBackend(s).candidates(s['source_id'],0)['candidates'][0]
    assert candidate['native_area_m2'] == pytest.approx(100)
    assert candidate['calibration']['kind'] == 'native_dxf_units'
    path.write_text('changed original')
    # Extraction snapshots preserve original bytes independently of caller paths.
    assert RegisteredNativeBackend(s).candidates(s['source_id'],0)['candidates']


def test_unitless_conflict_open_and_curve(tmp_path):
    path, handle = source(tmp_path, 0)
    missing = extract(path, {'element_ids':[handle]})
    assert missing['rooms'][0]['area_m2'] is None
    assert 'scale_confirmation_required' in missing['quality_flags']
    assert extract(path, {'element_ids':[handle], 'dxf_unit':'mm'})['rooms'][0]['area_m2'] == pytest.approx(.0001)
    doc = ezdxf.readfile(path); doc.units=6
    circle = doc.modelspace().add_circle((30,30),10)
    opened = doc.modelspace().add_lwpolyline([(0,0),(5,0),(5,5)])
    doc.saveas(path)
    curve = extract(path, {'element_ids':[circle.dxf.handle]})['rooms'][0]
    assert curve['estimate'] and curve['limitations']
    assert curve['area_m2'] == pytest.approx(math.pi*100, rel=.002)
    assert extract(path, {'element_ids':[opened.dxf.handle]})['rejections']
    assert extract(path, {'element_ids':[handle], 'dxf_unit':'ft'})['rejections']


def test_block_transform_and_document_reload(tmp_path):
    doc=ezdxf.new(); doc.units=6
    block=doc.blocks.new('ROOM')
    block.add_lwpolyline([(0,0),(4,0),(4,3),(0,3)], close=True)
    insert=doc.modelspace().add_blockref('ROOM',(20,30),dxfattribs={'rotation':90,'xscale':2,'yscale':3})
    stream=io.StringIO();doc.write(stream);raw=stream.getvalue().encode()
    drawings=DrawingStore(uploads=tmp_path/'drawings')
    documents=DocumentStore(drawings,tmp_path/'documents')
    upload=documents.upload(raw,'drawing.dxf')
    assert upload['selectors'][0]['id']==insert.dxf.handle
    output=documents.prepare(upload['id'],SheetSelection(options={'element_ids':[insert.dxf.handle]}))
    saved=drawings.raw(output['sheets'][0]['id'])['lens_source']['native_source']
    documents=DocumentStore(drawings,tmp_path/'documents')
    assert documents.original(upload['id']).read_bytes()==raw
    candidate=RegisteredNativeBackend(saved).candidates(saved['source_id'],0)['candidates'][0]
    assert candidate['native_area_m2']==pytest.approx(72)
    with open(saved['native_geometry']['path'],'ab') as f:f.write(b'changed')
    with pytest.raises(ValueError,match='changed'):
        RegisteredNativeBackend(saved).candidates(saved['source_id'],0)


def test_dxf_job_snapshot_measurement_answer_and_overlay(tmp_path):
    import time
    from ymir.lens_jobs import JobStore
    path, handle = source(tmp_path)
    drawings=DrawingStore(uploads=tmp_path/'drawings')
    documents=DocumentStore(drawings,tmp_path/'documents')
    upload=documents.upload(path.read_bytes(),'room.dxf')
    prepared=documents.prepare(upload['id'],SheetSelection(options={'element_ids':[handle]}))
    drawing_id=prepared['sheets'][0]['id']
    def runner(pages, question, folder, **hooks):
        registry=Registry(pages,folder/'views');pid=registry.initial['page_id']
        geometry=Geometry(tmp_path,folder/'geometry',registry)
        try:
            candidates=geometry.native_room_candidates(pid)['candidates']
            c=candidates[0];cal=c['calibration']['calibration_id']
            measured=geometry.measure_room(c['candidate_id'],cal)
            assert measured['area_m2']==pytest.approx(100)
            return {'status':'answered','counts':[], 'accepted_proposal_ids':[], 'proposals':[],
                    'rooms':[{'page_id':pid,'room_id':'r','candidate_id':c['candidate_id'],
                              'polygon':c['polygon']['exterior'],'holes':c['polygon']['holes'],
                              'label':c['label'],'measurement':measured,'area_m2':measured['area_m2']}],
                    'measurements':[], 'readings':[], 'text_evidence':[]}
        finally:geometry.close()
    jobs=JobStore(drawings,documents,tmp_path/'jobs',runner=runner)
    try:
        ident=jobs.submit({'request_id':'dxf-job','drawing_id':drawing_id,'selected_drawing_ids':[drawing_id],
                          'question':'Measure room area','project_id':'dxf-project','conversation_id':'dxf-conversation','prior_job_ids':[]})
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            result=jobs.lookup(ident)
            if result['status'] in ('completed','failed'):break
            time.sleep(.01)
        assert result['status']=='completed',result
        answer=result['answer']
        assert answer['rooms'][0]['area_m2']==pytest.approx(100)
        assert answer['rooms'][0]['measurement']['measurement_id']
        assert answer['citations'][0]['polygon']
    finally:jobs.stop()
