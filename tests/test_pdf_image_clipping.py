"""Document intake reports PDF page overflow without changing its contents."""
import hashlib
import io

import pymupdf
import pytest
from PIL import Image

from ymir.documents import DocumentStore
from ymir.drawings import DrawingStore


def pdf_with_image(placement, rotation=0):
    image = io.BytesIO()
    Image.new('RGB', (10, 10), 'white').save(image, format='PNG')
    with pymupdf.open() as doc:
        page=doc.new_page(width=200,height=100)
        page.insert_image(pymupdf.Rect(*placement),stream=image.getvalue(),keep_proportion=False)
        page.set_rotation(rotation)
        return doc.tobytes()


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_clipped_placement_warned_with_rotated_page(tmp_path,rotation):
    raw=pdf_with_image((30,10,230,90),rotation)
    store=DocumentStore(DrawingStore(samples=tmp_path/'samples',uploads=tmp_path/'drawings'),tmp_path/'documents')
    doc=store.upload(raw,'clipped.pdf')
    assert doc['pages'][0]['off_page_image_placements']
    assert doc['pages'][0]['off_page_image_placements'][0]['coordinate_space']=='rotated_page_points'
    assert any('Page 1' in message and 'outside' in message for message in doc['limitations'])
    assert store.original(doc['id']).read_bytes()==raw
    assert doc['sha256']==hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_fully_visible_placement_has_no_false_warning(tmp_path,rotation):
    raw=pdf_with_image((20,10,180,90),rotation)
    store=DocumentStore(DrawingStore(samples=tmp_path/'samples',uploads=tmp_path/'drawings'),tmp_path/'documents')
    doc=store.upload(raw,'visible.pdf')
    assert doc['pages'][0]['off_page_image_placements']==[]
    assert not doc['limitations']


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_page_crop_is_respected_before_rotation(tmp_path,rotation):
    image=io.BytesIO();Image.new('RGB',(10,10),'white').save(image,format='PNG')
    with pymupdf.open() as doc:
        page=doc.new_page(width=200,height=100)
        page.insert_image(pymupdf.Rect(30,10,190,90),stream=image.getvalue(),keep_proportion=False)
        page.set_cropbox(pymupdf.Rect(20,10,180,90));page.set_rotation(rotation)
        raw=doc.tobytes()
    store=DocumentStore(DrawingStore(samples=tmp_path/'samples',uploads=tmp_path/'drawings'),tmp_path/'documents')
    result=store.upload(raw,'cropped.pdf')
    assert result['pages'][0]['off_page_image_placements']
