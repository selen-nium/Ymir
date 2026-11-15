"""One-time cutover: copy existing jobs, reviews, sources and the unchanged budget.

Stop the local API first. Existing remote state is never overwritten.
"""
from pathlib import Path
import argparse
import fcntl
import shutil
import sqlite3
import tempfile
import modal


def upload(root):
    if not (root/'lens/budget.sqlite').is_file():raise ValueError('Existing budget ledger required')
    with (root/'lens/worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with sqlite3.connect(root/'lens/jobs.sqlite') as db:
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]:
                raise ValueError('Finish or cancel pending jobs before cutover')
        with tempfile.TemporaryDirectory(prefix='ymir-cutover-') as folder:
            stage=Path(folder)
            for name in ('lens','drawings','documents'):
                if (root/name).exists():shutil.copytree(root/name,stage/name,ignore=shutil.ignore_patterns('worker.lock','*.log','*-wal','*-shm'))
            if (root/'ymir.db').exists():shutil.copy2(root/'ymir.db',stage/'ymir.db')
            # Use SQLite's online backup API so retained WAL pages are included.
            for source in [*root.glob('*.db'),*root.glob('lens/*.sqlite')]:
                target=stage/source.relative_to(root)
                if target.exists():target.unlink()
                with sqlite3.connect(source) as origin,sqlite3.connect(target) as destination:
                    origin.backup(destination)
            volume=modal.Volume.from_name('ymir-data',create_if_missing=True)
            with volume.batch_upload(force=False) as batch:batch.put_directory(stage,'/')
    print('Existing Ymir state uploaded. Keep the local API stopped while the hosted writer is active.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--state',type=Path,default=Path(__file__).resolve().parents[1]/'var')
    upload(parser.parse_args().state)
