"""Live Build does not dispatch retired text or vision tools."""
import io
from PIL import Image, ImageDraw


def test_build_defaults_to_geometry_without_legacy_text_dispatch(monkeypatch):
    from ymir.build import ocr
    from ymir.build.pipeline import build_drawing
    def forbidden(*args, **kwargs):
        raise AssertionError('Live Build must not dispatch retired OCR')
    monkeypatch.setattr(ocr,'available',forbidden)
    monkeypatch.setattr(ocr,'ocr_image',forbidden)
    image=Image.new('RGB',(240,180),'white');draw=ImageDraw.Draw(image);draw.rectangle((20,20,220,160),outline='black',width=6)
    raw=io.BytesIO();image.save(raw,format='PNG')
    built=build_drawing(raw.getvalue(),drawing_id='runtime-test',title='Plan',dataset='Analytic fixture',kind='Drawing',file='fixture.png',crop=(0,0,240,180),detector='lines')
    nodes=[e for e in built.trace if e['type']=='node']
    assert {e['id'] for e in nodes}=={'read','plan','walls','scale','rooms'}
    assert not any('claude' in str(e).lower() or 'rapidocr' in str(e).lower() for e in built.trace)
    assert built.payload['texts']==[]
