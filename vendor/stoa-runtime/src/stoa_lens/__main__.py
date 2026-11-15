"""Backend-only Stoa Lens commands. Metered runs require an explicit ceiling."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
from pathlib import Path
import sys

from .budget import LensBudget
from .service import ROOT, run


def dump(path, value):
    path = Path(path)
    with path.open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def _packet_coverage(data):
    pages = data['pages']
    if not isinstance(pages, list) or not pages:
        raise ValueError('Packet must include at least one page')
    total, included = data.get('total_source_pages'), data.get('included_pages')
    if total is None and included is None:
        return pages
    if (type(total) is not int or type(included) is not int or
        not 1 <= included <= total or included != len(pages)):
        raise ValueError('Packet page coverage metadata is inconsistent')
    if any(type(page.get('source_page')) is not int or not 1 <= page['source_page'] <= total for page in pages):
        raise ValueError('Packet source-page identity is inconsistent')
    if len({page['source_page'] for page in pages}) != len(pages):
        raise ValueError('Packet repeats a source page')
    return [{**page, 'document_page_count':total, 'packet_included_pages':included} for page in pages]


def packet_pages(args):
    if args.packet:
        path = args.packet.resolve()
        data = json.loads(path.read_text())
        # Repository packets use paths relative to the repository root.
        return _packet_coverage(data)
    if args.native_result:
        source = json.loads(args.native_result.read_text())['source']
        return [{'native_source': source, 'image': source['path'], 'width': source['width'],
                 'height': source['height'], 'source_page': 1}]
    if not 1 <= args.max_pages <= 30:
        raise ValueError('max-pages must be between 1 and 30')
    if args.input.suffix.lower() == '.pdf':
        import pymupdf
        with pymupdf.open(args.input) as document:
            total = len(document)
        if total > args.max_pages:
            raise ValueError(f'PDF has {total} pages, exceeding --max-pages={args.max_pages}; increase the limit or use an explicitly selected packet')
    from stoa.packets import ingest
    path = ingest(args.input, args.packet_name or args.output.name, max_pages=args.max_pages)
    return _packet_coverage(json.loads(path.read_text()))


def _budget_args(parser):
    parser.add_argument('--budget', type=float, required=True, help='Explicit US dollar ceiling; existing ledgers must use their original ceiling.')
    parser.add_argument('--ledger', type=Path, required=True, help='Durable Lens-only SQLite ledger; reuse it to share a ceiling across jobs.')


def _read_results(manifest, jobs):
    results = {}
    for case in manifest['cases']:
        ident = case['case_id']
        path = jobs / ident / 'service-result.json'
        failed = jobs / ident / 'service-failure.json'
        if path.exists():
            receipt = json.loads(path.read_text())
            if receipt.get('status') == 'completed':
                results[ident] = receipt['result']
            else:
                results[ident] = {'status':'failed', 'error_type':'ResourceCleanupFailed' if receipt.get('status') == 'cleanup_failed' else 'InvalidServiceReceipt',
                                  'partial_result':receipt.get('result')}
        elif failed.exists():
            results[ident] = json.loads(failed.read_text())
    return results


def score_pilot(args):
    from .pilot import score
    manifest = json.loads(args.manifest.read_text())
    references = json.loads(args.references.read_text())
    results = _read_results(manifest, args.jobs)
    value = score(manifest, results, references)
    args.output.mkdir(parents=True, exist_ok=False)
    dump(args.output / 'metrics.json', value)
    with (args.output / 'metrics.csv').open('x') as f:
        writer = csv.DictWriter(f, fieldnames=['case_id', 'status', 'answered_count', 'official_count', 'correct'])
        writer.writeheader()
        writer.writerows({k: r[k] for k in writer.fieldnames} for r in value['rows'])
    rows = []
    for row in value['rows']:
        ident = row['case_id']
        result = results.get(ident, {})
        rows.append('<tr>' + ''.join('<td>' + html.escape(str(x)) + '</td>' for x in
                   (ident, row['status'], row['answered_count'], row['official_count'], row['correct'],
                    round(result.get('elapsed_seconds', 0), 2))) + '</tr>')
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Stoa Lens pilot</title>'
            '<style>body{font:16px system-ui;margin:2rem;max-width:1000px}td,th{padding:.6rem;text-align:left;border-bottom:1px solid #ccc}</style>'
            '<h1>Stoa Lens historical pilot rerun</h1><p>' + html.escape(str(value['correct'])) + '/' + str(value['planned']) +
            ' official counts correct. Previously exposed diagnostics; runtime and prompts differ from the historical baseline.</p>'
            '<table><thead><tr><th>Case<th>Status<th>Answer<th>Reference<th>Correct<th>Seconds</tr></thead><tbody>' +
            ''.join(rows) + '</tbody></table><p>Box metrics in metrics.json use AI-reviewed diagnostic inventories, not official boxes.</p></html>')
    (args.output / 'index.html').write_text(page)
    return value



def validate_pilot_manifest(manifest):
    """Verify only frozen PUBLIC inputs; private scoring references stay offline."""
    from .pilot import IDS, PUBLIC, PUBLIC_SHA, checked, sha
    if set(manifest) != {'protocol','selection','source_public_sha256','exposure','cases'}:
        raise ValueError('Unexpected pilot manifest fields')
    if (manifest.get('protocol')!='stoa-lens-rfdetr-nine-case-reuse-v1' or
        manifest.get('source_public_sha256')!=PUBLIC_SHA or
        manifest.get('selection')!='Exact historical nine-case RF-DETR/VISTA pilot, including AECV schedule' or
        manifest.get('exposure')!='Previously exposed diagnostics; no held-out claim'):
        raise ValueError('Pilot public manifest provenance differs')
    public=checked(ROOT,PUBLIC,PUBLIC_SHA)
    if tuple(c.get('case_id') for c in manifest.get('cases',[]))!=IDS:
        raise ValueError('Pilot case selection or order differs')
    for case,row in zip(manifest['cases'],public):
        kind=row['target_kind']
        classes={'toilets':['toilet_bowl'],'windows':['window'],'doors':['door'],
                 'baths':['bathtub'] if 'Swiss' in row['dataset'] else ['bathtub','shower'],
                 'footing_type':[]}[kind]
        expected_pages=[]
        for page in row['pages']:
            image=(ROOT/page['image']).resolve()
            if sha(image)!=page['image_sha256']:raise ValueError('Pilot source hash changed: '+case['case_id'])
            expected_pages.append({**page,'image':str(image)})
        expected={'case_id':row['id'],'dataset':row['dataset'],'group_id':row['group_id'],
                  'exposure':'previously_exposed_diagnostic','question':row['question'],
                  'scope_instruction':row['scope_instruction'],'target_kind':kind,'target_classes':classes,
                  'task_type':'schedule_count' if kind=='footing_type' else 'object_count',
                  'schedule_behavior':'Use visible schedule evidence; object detection does not count schedule rows.' if kind=='footing_type' else None,
                  'pages':expected_pages,'baseline':{'study':'rfdetr-vista-deepseek-v1','case_id':row['id'],
                  'comparison':'historical diagnostic; runtime and prompts differ'}}
        if case!=expected:raise ValueError('Pilot public case differs: '+case['case_id'])

def run_pilot(args):
    """Dispatch only public inputs. Offline references are never opened here."""
    path = args.manifest.resolve()
    raw = path.read_bytes()
    manifest = json.loads(raw)
    validate_pilot_manifest(manifest)
    if args.output.exists():raise FileExistsError('Pilot job directory already exists')
    budget = LensBudget(args.ledger, args.budget)
    args.output.mkdir(parents=True, exist_ok=False)
    dump(args.output / 'public-manifest.json', manifest)
    dump(args.output / 'run-config.json', {'manifest_sha256': hashlib.sha256(raw).hexdigest(),
         'budget_cap_usd': args.budget, 'ledger': str(args.ledger.resolve()), 'automatic_retries': False,
         'execution': 'sequential; shared provider pacing and local GPU lease', 'reference_access': 'offline scoring only'})
    results = {}
    for case in manifest['cases']:
        # Reserved or unknown charges remain committed in this one shared ledger.
        if budget.summary()['remaining_usd'] <= 0:
            break
        question = case['question'] + '\n' + case['scope_instruction']
        if case['task_type'] == 'schedule_count':
            question += '\nReturn the schedule count as a reading with unit="count", citing registered view or PDF evidence.'
        try:
            results[case['case_id']] = run(case['pages'], question, args.output / case['case_id'], budget=budget)
        except Exception as exc:
            results[case['case_id']] = {'status': 'failed', 'error_type': type(exc).__name__}
        partial = args.output / 'progress.json'
        temp = partial.with_suffix('.tmp')
        temp.write_text(json.dumps({'results': results, 'budget': budget.summary()}, ensure_ascii=False, indent=2))
        temp.replace(partial)
    dump(args.output / 'budget-after.json', budget.summary())
    return {'attempted': len(results), 'planned': len(manifest['cases']), 'statuses': {k: v['status'] for k, v in results.items()},
            'budget': budget.summary(), 'jobs': str(args.output.resolve())}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    ask = sub.add_parser('ask', help='Run a question on a packet, image/PDF, or prepared native footprint preview.')
    source = ask.add_mutually_exclusive_group(required=True)
    source.add_argument('--packet', type=Path, help='Existing packet.json; repository-relative asset paths.')
    source.add_argument('--input', type=Path, help='New image/PDF to ingest into data/packets.')
    source.add_argument('--native-result', type=Path, help='Existing stoa-native-geometry prepared result.json.')
    ask.add_argument('--packet-name', help='Unique ingestion name when using --input.')
    ask.add_argument('--max-pages', type=int, default=3)
    ask.add_argument('--question', required=True)
    ask.add_argument('--agent', choices=('general','counter','rooms','compact_counter'), default='general',
                     help='Task-specific DeepSeek prompt/tools/schema; general preserves the original harness.')
    ask.add_argument('--bound-page-index',type=int,help='Zero-based page index; compact counting requires one page and index 0.')
    ask.add_argument('--bound-class',action='append',help='Host-bound target class; repeat for a group. Routed groups use one detector request per class.')
    ask.add_argument('--bound-description',help='Human description of the bound target group.')
    ask.add_argument('--bound-backend',choices=('routed','kimi_atomic'),help='Default routed product policy; kimi_atomic is an explicit diagnostic.')
    ask.add_argument('--output', type=Path, required=True, help='Fresh immutable job directory.')
    _budget_args(ask)
    prepare = sub.add_parser('pilot-prepare', help='Freeze the original nine-case pilot; no model calls.')
    prepare.add_argument('--output', type=Path, required=True)
    pilot = sub.add_parser('pilot-run', help='Run public pilot cases sequentially; no automatic retries.')
    pilot.add_argument('--manifest', type=Path, required=True)
    pilot.add_argument('--output', type=Path, required=True)
    _budget_args(pilot)
    score = sub.add_parser('pilot-score', help='Offline-only reference scoring, JSON/CSV and HTML.')
    score.add_argument('--manifest', type=Path, required=True)
    score.add_argument('--references', type=Path, required=True)
    score.add_argument('--jobs', type=Path, required=True)
    score.add_argument('--output', type=Path, required=True)
    catalog = sub.add_parser('catalog', help='Refresh authenticated model availability/pricing; no inference calls.')
    catalog.add_argument('--output', type=Path, default=ROOT / 'data/stoa-lens/provider')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'ask':
            if args.output.exists():
                raise FileExistsError('Job directory already exists')
            fields=(args.bound_page_index,args.bound_class,args.bound_description,args.bound_backend)
            if any(x is not None for x in fields):
                if args.agent!='compact_counter' or args.bound_page_index is None or not args.bound_class or not args.bound_description:
                    raise ValueError('Use --agent compact_counter with --bound-page-index, --bound-class, and --bound-description')
                count_scope={'page_index':args.bound_page_index,'target_classes':args.bound_class,
                             'description':args.bound_description,'backend':args.bound_backend or 'routed'}
            else:
                if args.agent=='compact_counter':raise ValueError('compact_counter requires explicit bound scope')
                count_scope=None
            budget = LensBudget(args.ledger, args.budget)
            value = run(packet_pages(args), args.question, args.output, budget=budget,agent=args.agent,count_scope=count_scope)
        elif args.command == 'pilot-prepare':
            from .pilot import prepare
            value = prepare(ROOT, args.output)
        elif args.command == 'pilot-run':
            value = run_pilot(args)
        elif args.command == 'pilot-score':
            value = score_pilot(args)
        else:
            from .transport import refresh_pricing
            value = refresh_pricing(ROOT, args.output)
    except KeyboardInterrupt:
        print('Interrupted; evidence and pending reservations are preserved.', file=sys.stderr)
        return 130
    except Exception as exc:
        print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
