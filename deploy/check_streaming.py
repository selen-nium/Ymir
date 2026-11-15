"""Disposable Modal streaming check. No production data, secrets or inference."""
from pathlib import Path
import modal

ROOT=Path(__file__).resolve().parents[1]
app=modal.App('ymir-stream-validation')
image=(modal.Image.debian_slim(python_version='3.12').uv_pip_install('fastapi[standard]')
       .add_local_file(ROOT/'backend/ymir/lens_stream.py','/root/lens_stream.py',copy=True))


@app.function(image=image,cpu=(.25,.25),memory=256,timeout=30,max_containers=1,scaledown_window=2)
@modal.concurrent(max_inputs=8)
@modal.asgi_app()
def web():
    import asyncio,json
    from fastapi import FastAPI,Request
    from lens_stream import event_response
    api=FastAPI()
    @api.get('/probe')
    async def probe(request: Request):
        async def events():
            yield json.dumps({'type':'job','job_id':'fixture','status':'running','seq':1})+'\n'
            await asyncio.sleep(2)
            yield json.dumps({'type':'progress','activity':{'operation':'tool','tool':'predict_room_polygons','phase':'started'},'seq':2})+'\n'
            await asyncio.sleep(2)
            yield json.dumps({'type':'answer','answer':{'status':'answered','text':'Streaming validation fixture'},'seq':3})+'\n'
        return event_response(events(),request.headers.get('accept',''))
    return api
