"""Moving a workspace preserves records and resolves source files without inference."""
import hashlib
import json
from pathlib import Path

from ymir.drawings import DrawingStore


def test_uploaded_document_paths_follow_workspace_without_rewriting_records(tmp_path):
    root=tmp_path/'hosted';uploads=root/'drawings';uploads.mkdir(parents=True)
    document=root/'documents/doc-one/prepare-first';document.mkdir(parents=True)
    for name in ('page.png','page.pdf','source.ifc','extraction.json'):(document/name).write_bytes(name.encode())
    old=Path('/previous/workspace/var/documents/doc-one/prepare-first')
    source={'document_id':'doc-one','image':str(old/'page.png'),'pdf':str(old/'page.pdf'),
            'source_sha256':'unchanged-source-hash','image_sha256':'unchanged-image-hash',
            'native_source':{'path':str(old/'page.png'),'native_geometry':{
                'path':str(old/'source.ifc'),'extraction_path':str(old/'extraction.json')},
                'native_to_page':[1,0,0,1,0,0]}}
    path=uploads/'u-test.json';path.write_text(json.dumps({'id':'u-test','lens_source':source}))
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    result=DrawingStore(samples=tmp_path/'samples',uploads=uploads).raw('u-test')['lens_source']
    assert result['image']==str(document/'page.png') and result['pdf']==str(document/'page.pdf')
    assert result['native_source']['path']==str(document/'page.png')
    assert result['native_source']['native_geometry']['path']==str(document/'source.ifc')
    assert result['native_source']['native_geometry']['extraction_path']==str(document/'extraction.json')
    assert result['source_sha256']==source['source_sha256']
    assert result['native_source']['native_to_page']==[1,0,0,1,0,0]
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


def test_resolution_does_not_use_another_document_or_missing_assets(tmp_path):
    uploads=tmp_path/'drawings';uploads.mkdir()
    doc=tmp_path/'documents/doc-one';doc.mkdir(parents=True)
    (doc/'page.png').write_bytes(b'image')
    source={'document_id':'doc-one','image':'/old/documents/doc-two/page.png',
            'pdf':'/old/documents/doc-one/missing.pdf',
            'outside':'/old/documents/doc-one/../outside.png','url':'https://example.com/documents/doc-one/page.png'}
    store=DrawingStore(samples=tmp_path/'samples',uploads=uploads)
    assert store._relocate_uploaded_source(source)==source


def test_current_sources_and_bundled_sources_keep_their_existing_resolution(tmp_path):
    uploads=tmp_path/'drawings';uploads.mkdir()
    doc=tmp_path/'documents/doc-one';doc.mkdir(parents=True)
    image=doc/'page.png';image.write_bytes(b'image')
    store=DrawingStore(samples=tmp_path/'samples',uploads=uploads)
    source={'document_id':'doc-one','image':str(image)}
    assert store._relocate_uploaded_source(source)==source
    store.samples.mkdir();(store.samples/'sample.json').write_text(json.dumps({'lens_source':{'image':'sample.png'}}))
    assert store.raw('sample')['lens_source']['image']==str(store.samples/'sample.png')


def test_library_supports_head_for_cors_verification():
    from fastapi.testclient import TestClient
    from ymir.app import app
    response=TestClient(app).head('/api/library')
    assert response.status_code==200 and response.content==b''
