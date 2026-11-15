"""Publish a compact proof from a completed real-GPU smoke report."""
from pathlib import Path
import argparse
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]


def publish(path):
    raw=json.loads(path.read_text())
    if raw.get('passed') is not True:raise ValueError('A successful live GPU report is required')
    dfine=raw['models']['dfine'];raster=raw['models']['raster2seq']
    receipt={k:v for k,v in dfine['receipt'].items() if k!='all_class_predictions'}
    keys=('passed','checkpoint_sha256','checkpoint_coverage','source_commit','source_tree_sha256',
          'cuda_extensions','worker_sha256','gpu','torch','cuda','batch_size','loader_workers','runtime_seconds')
    result={'schema':'ymir-modal-readiness-v1','passed':True,'started_unix':raw['started_unix'],
            'report_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'asset_manifest_sha256':hashlib.sha256((ROOT/'deploy/model-assets.json').read_bytes()).hexdigest(),
            'source_sha256':raw['source_sha256'],'reference_annotations_provided':False,'automatic_retries':False,
            'models':{'dfine':{'passed':True,'receipt':receipt},'raster2seq':{k:raster[k] for k in keys}}}
    target=ROOT/'deploy/model-readiness.json';target.write_text(json.dumps(result,indent=2)+'\n')
    print(target)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('report',type=Path)
    publish(parser.parse_args().report)
