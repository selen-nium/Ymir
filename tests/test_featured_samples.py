"""Prepared sample catalog and original witnesses, with no inference dispatch."""
import hashlib
import json
from pathlib import Path

from ymir.drawings import DrawingStore
from ymir.documents import DocumentStore
from stoa_lens.registry import Registry


def test_featured_catalog_preserves_legacy_links_and_all_requested_families(tmp_path):
    drawings=DrawingStore(uploads=tmp_path/'uploads');documents=DocumentStore(drawings,tmp_path/'documents')
    assert [d['id'] for d in drawings.library()]==['sample-cubicasa-2536','sample-swiss-5733']
    projects=drawings.projects()
    assert {p['id'] for p in projects}=={'junction-ac20','junction-duplex','junction-digitalhub','junction-sports-complex'}
    for ident in ['cubi','swiss','finna','ac20-eg']:assert drawings.raw(ident)['id']==ident
    assert drawings.project('ac20')['id']=='ac20'
    for project in projects:
        full=drawings.project(project['id'])
        for sheet in full['sheets']:
            d=drawings.raw(sheet['drawing_id']);source=d['lens_source'];assert d['source']['prepared']
            assert hashlib.sha256(drawings.image_path(d['id']).read_bytes()).hexdigest()==source['image_sha256']
            assert hashlib.sha256(documents.original(source['document_id']).read_bytes()).hexdigest()==source['source_sha256']
            assert d['source'].get('credit') and d['source'].get('licence')
        if project['id']!='junction-sports-complex':
            assert all(s['views'][0]['placement'].get('transform') for s in full['sheets'])


def test_cached_sol_sources_and_native_pdf_registration(tmp_path):
    drawings=DrawingStore(uploads=tmp_path/'uploads')
    for ident in ['sample-cubicasa-2536','sample-swiss-5733']:
        d=drawings.raw(ident);assert d['source']['prior_run']['model'] in ('sol','gpt-6.1-sol')
        assert d['source']['prior_run']['status']=='completed'
        assert d['walls'] and d['rooms']
        Registry([d['lens_source']],tmp_path/ident)
    # Packet page numbers and full 33-page coverage stay truthful in curated selections.
    sheets=[drawings.raw(ident)['lens_source'] for ident in ['sports-a-101-a','sports-a-801']]
    for source in sheets:source['packet_included_pages']=2
    registry=Registry(sheets,tmp_path/'sports')
    assert {p['source_page'] for p in registry.sources.values()}=={10,33}
    assert all(p['document_page_count']==33 for p in registry.sources.values())
