"""One idempotent live follow-up, sharing the integration ledger and conversation."""
import json
from pathlib import Path
import requests

home=Path(__file__).resolve().parents[1]/'var/live-checks'
case=json.loads((home/'selection.json').read_text())['cases'][0]
first=[json.loads(s) for s in (home/'aecv-doors.events.jsonl').read_text().splitlines()]
prior=next(e['job_id'] for e in first if e['type']=='job')
body={'request_id':'ymir-live-v1-aecv-followup','selected_drawing_ids':[case['drawing_id']],'drawing_id':case['drawing_id'],
      'question':'And what printed area is shown for the GARAGE on the left? Report a printed-area reading, not a geometric measurement.',
      'project_id':'lens-live-aecv-doors','conversation_id':'lens-live-aecv-doors','prior_job_ids':[prior]}
with requests.post('http://127.0.0.1:8011/api/ask',json=body,stream=True,timeout=(10,700)) as response:
    response.raise_for_status()
    with (home/'aecv-followup.events.jsonl').open('w') as out:
        for line in response.iter_lines():
            if not line:continue
            event=json.loads(line);out.write(line.decode()+'\n');out.flush()
            if event['type'] in ('job','answer'):print(json.dumps(event)[:500],flush=True)
