"""Prepare an IFC/SVG native source and a source-bound room-footprint preview."""
import argparse
import json
from pathlib import Path
import sys

from .backend import prepare, _prepare_worker


def main():
    if len(sys.argv)==3 and sys.argv[1]=='--worker':
        folder=Path(sys.argv[2]); _prepare_worker(json.loads((folder/'request.json').read_text()),folder); return
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--options',type=Path,help='JSON: element_ids, storey_id and/or svg_scale length confirmation.')
    parser.add_argument('--project-id')
    parser.add_argument('--dataset')
    parser.add_argument('--exposure',default='user_upload')
    args=parser.parse_args()
    result=prepare(args.file,args.output,options=json.loads(args.options.read_text()) if args.options else None,
                   project_identity=args.project_id,dataset=args.dataset,exposure=args.exposure)
    print(json.dumps({'status':result['extraction']['status'],'rooms':len(result['extraction']['rooms']),
                      'result':str((args.output/'result.json').resolve())},indent=2))


if __name__=='__main__':main()
