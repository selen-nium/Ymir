"""Original bytes and coordinate registration survive automatic working-image resizing."""
import io
import math

import pytest
from PIL import Image

from ymir import documents
from ymir.documents import DocumentStore,SheetSelection,prepare_raster,transform_point
from ymir.drawings import DrawingStore


@pytest.mark.parametrize('orientation',range(1,9))
def test_raster_resize_and_exif_registration_roundtrip(tmp_path,monkeypatch,orientation):
    monkeypatch.setattr(documents,'MAX_SHEET_IMAGE_PIXELS',20_000)
    image=Image.new('RGB',(400,200),'white');exif=image.getexif();exif[274]=orientation
    path=tmp_path/'original.png';image.save(path,exif=exif)
    original=path.read_bytes();target=tmp_path/'working.png'
    registration=prepare_raster(path,target)
    assert registration['resized'] and math.prod(registration['sheet_size'])<=20_000
    assert registration['original_size']==[400,200]
    assert registration['oriented_size']==([200,400] if orientation>=5 else [400,200])
    for p in ([0,0],[400,0],[400,200],[0,200],[73,121]):
        projected=transform_point(p,registration['original_to_sheet'])
        assert 0<=projected[0]<=registration['sheet_size'][0] and 0<=projected[1]<=registration['sheet_size'][1]
        assert transform_point(projected,registration['sheet_to_original'])==pytest.approx(p)
    assert path.read_bytes()==original


def test_image_over_working_limit_imports_and_records_transforms(tmp_path,monkeypatch):
    monkeypatch.setattr(documents,'MAX_SHEET_IMAGE_PIXELS',20_000)
    image=Image.new('RGB',(400,200),'white');raw=io.BytesIO();image.save(raw,format='PNG')
    drawings=DrawingStore(uploads=tmp_path/'drawings');store=DocumentStore(drawings,tmp_path/'documents')
    doc=store.upload(raw.getvalue(),'large-plan.png');prepared=store.prepare(doc['id'],SheetSelection())
    payload=drawings.raw(prepared['sheets'][0]['id'])
    registration=payload['source']['image_registration']
    assert registration==payload['lens_source']['image_registration']
    assert payload['image']['size']==registration['sheet_size']
    assert payload['lens_source']['source_sha256']==doc['sha256']
    assert store.original(doc['id']).read_bytes()==raw.getvalue()


def test_huge_image_error_distinguishes_resolution_from_file_size(tmp_path,monkeypatch):
    monkeypatch.setattr(documents,'MAX_ORIGINAL_IMAGE_PIXELS',10_000)
    image=Image.new('RGB',(200,100),'white');raw=io.BytesIO();image.save(raw,format='PNG')
    store=DocumentStore(DrawingStore(uploads=tmp_path/'drawings'),tmp_path/'documents')
    with pytest.raises(ValueError,match='Image resolution.*megapixels.*pixels'):
        store.upload(raw.getvalue(),'too-many-pixels.png')
    assert list((tmp_path/'documents').glob('*/intake-failure.json'))
    assert list((tmp_path/'documents').glob('*/original.png'))[0].read_bytes()==raw.getvalue()
