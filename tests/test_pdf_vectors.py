"""PDF source commands remain aligned with full-page display under crop/rotation."""
import pytest
import pymupdf
from stoa_lens.registry import Registry
from stoa_lens.native_vectors import read_pdf_vectors
from stoa_lens.agents import capabilities, profile


@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_registered_pdf_vectors_and_visual_capabilities(tmp_path,rotation):
    doc=pymupdf.open();p=doc.new_page(width=300,height=200)
    p.draw_line((50,60),(120,60))
    p.draw_rect(pymupdf.Rect(60,80,130,140))
    p.insert_text((70,100),'Kitchen')
    p.set_cropbox(pymupdf.Rect(20,20,280,180));p.set_rotation(rotation)
    pdf=tmp_path/'source.pdf';doc.save(pdf);doc.close()
    with pymupdf.open(pdf) as doc:
        pix=doc[0].get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False,colorspace=pymupdf.csRGB)
        image=tmp_path/'page.png';pix.save(image)
        original=doc[0].get_drawings()[0]['items'][0][1]
        expected=original*doc[0].rotation_matrix*pymupdf.Matrix(2,2)
    registry=Registry([{'image':str(image),'pdf':str(pdf),'width':pix.width,'height':pix.height}],tmp_path/'views')
    result=read_pdf_vectors(registry.initial)
    assert len(result['paths'])==2
    assert result['paths'][0]['commands'][0]['points'][0]==pytest.approx([expected.x,expected.y])
    assert result['paths'][0]['pdf_sha256']==registry.initial['pdf_sha256']
    assert result['paths'][0]['geometry_id']==read_pdf_vectors(registry.initial)['paths'][0]['geometry_id']
    caps=capabilities(registry)[0]
    assert caps['native_pdf_vectors'] and caps['object_localization'] and caps['raster_room_fallback']
    assert not caps['native_room_geometry']
    assert 'read_pdf_vectors' in profile('rooms',registry).tools
    with open(pdf,'ab') as f:f.write(b'changed')
    with pytest.raises(ValueError,match='hash mismatch'):read_pdf_vectors(registry.initial)


def test_vector_pagination_and_preserved_cubic(tmp_path):
    doc=pymupdf.open();p=doc.new_page()
    for i in range(53):p.draw_line((10,i+10),(20,i+10))
    p.draw_bezier((10,100),(20,80),(30,120),(40,100))
    pdf=tmp_path/'source.pdf';doc.save(pdf)
    pix=p.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False,colorspace=pymupdf.csRGB)
    image=tmp_path/'page.png';pix.save(image);doc.close()
    registry=Registry([{'image':str(image),'pdf':str(pdf),'width':pix.width,'height':pix.height}],tmp_path/'views')
    first=read_pdf_vectors(registry.initial)
    assert len(first['paths'])==50 and first['next_cursor']==50
    second=read_pdf_vectors(registry.initial,first['next_cursor'])
    assert len(second['paths'])==4 and second['next_cursor'] is None
    assert second['paths'][-1]['commands'][0]['command']=='c'
    assert len(second['paths'][-1]['commands'][0]['points'])==4
    with pytest.raises(ValueError,match='Cursor'):read_pdf_vectors(registry.initial,55)
