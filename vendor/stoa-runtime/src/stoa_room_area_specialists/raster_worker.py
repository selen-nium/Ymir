"""One warm, offline, batch-one CUDA worker. JSON-lines stdin/stdout protocol."""
from __future__ import annotations

import contextlib
import copy
import json
import os
from pathlib import Path
import random
import sys
import time
import traceback


def load(root):
    home = root/'data/room-area-specialists/raster'
    upstream = home/'upstream'
    sys.path.insert(0, str(upstream))
    sys.path.insert(0, str(upstream/'models/ops'))
    sys.path.insert(0, str(upstream/'diff_ras'))
    # All network use is prohibited after explicit setup, including torch weights.
    import socket
    def offline(*args, **kwargs):
        raise RuntimeError('Network disabled in Raster2Seq inference worker')
    socket.socket.connect = offline
    socket.create_connection = offline
    import numpy as np
    import torch
    from predict import get_args_parser
    from datasets.discrete_tokenizer import DiscreteTokenizer
    from datasets.transforms import ResizeAndPad
    from detectron2.data import transforms as T
    from engine import generate
    from models import build_model
    from PIL import Image

    if not torch.cuda.is_available():
        raise RuntimeError('Actual local CUDA device required')
    if torch.__version__ != '2.3.1+cu118' or torch.version.cuda != '11.8':
        raise RuntimeError('Pinned Torch 2.3.1 / CUDA11.8 environment required')
    args = get_args_parser().parse_args([])
    configuration = json.loads((home/'assets/config.json').read_text())
    for key, value in configuration['inference_args'].items():
        setattr(args, key, value)
    if args.disable_poly_refine:
        args.with_poly_refine=False
    args.image_size=256
    args.batch_size=1
    args.num_workers=0
    args.device='cuda'
    torch.set_num_threads(1)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)
    tokenizer=DiscreteTokenizer(args.num_bins,args.seq_len,add_cls=args.add_cls_token)
    args.vocab_size=len(tokenizer)
    model=build_model(args,train=False,tokenizer=tokenizer)
    checkpoint=torch.load(home/'assets/checkpoint.pth',map_location='cpu')
    state=checkpoint['ema']
    state={key.removeprefix('module.'):value for key,value in state.items()}
    # Checkpoints also serialize transient decoding caches created by an earlier
    # evaluation. These are not learned parameters and are rebuilt per request.
    allowed_runtime_buffers={f'transformer.decoder.layers.{layer}.{suffix}'
        for layer in range(args.dec_layers)
        for suffix in ('kv_cache.k_cache','kv_cache.v_cache','cross_attn.cache.v_cache')}
    ignored_runtime_buffers={key:list(state[key].shape) for key in allowed_runtime_buffers if key in state}
    state={key:value for key,value in state.items() if key not in allowed_runtime_buffers}
    # No random or implicitly pretrained parameters are allowed to remain.
    missing,unexpected=model.load_state_dict(state,strict=False)
    if missing or unexpected:
        raise RuntimeError('Incomplete EMA checkpoint coverage: '+str(dict(missing=missing,unexpected=unexpected)))
    model.to('cuda').eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    coverage=dict(state_keys=len(state),model_state_keys=len(model.state_dict()),missing_keys=missing,unexpected_keys=unexpected,ema=True,ignored_runtime_buffers=ignored_runtime_buffers)
    del checkpoint,state
    transform=T.AugmentationList([ResizeAndPad((256,256),pad_value=255)])
    def predict(path):
        image=np.array(Image.open(path).convert('RGB'))
        height,width=image.shape[:2]
        scale=min(256/height,256/width)
        new_h,new_w=int(height*scale),int(width*scale)
        preprocessing=dict(original_width=width,original_height=height,resized_width=new_w,resized_height=new_h,left=(256-new_w)//2,top=(256-new_h)//2,model_size=256)
        aug=T.AugInput(image)
        transform(aug)
        tensor=(1/255)*torch.as_tensor(aug.image.transpose((2,0,1))).unsqueeze(0).to('cuda')
        torch.cuda.synchronize()
        start=time.monotonic()
        with torch.inference_mode():
            outputs=generate(model,tensor,semantic_rich=args.semantic_classes>0,use_cache=True,per_token_sem_loss=args.per_token_sem_loss,drop_wd=args.drop_wd,poly2seq=args.poly2seq)
        torch.cuda.synchronize()
        elapsed=time.monotonic()-start
        return dict(polygons=[p.tolist() for p in outputs['room'][0]],labels=[int(v) for v in outputs['labels'][0]],preprocessing=preprocessing,runtime_seconds=elapsed,checkpoint_coverage=coverage,gpu=torch.cuda.get_device_name(0),torch=torch.__version__,cuda=torch.version.cuda,batch_size=1,loader_workers=0,resolved_arguments=vars(args))
    return predict


def main():
    root=Path(sys.argv[1]).resolve()
    # Upstream modules and tokenizers print status messages; protocol stdout is clean.
    with contextlib.redirect_stdout(sys.stderr):
        predict=load(root)
    for line in sys.stdin:
        try:
            request=json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                result=predict(request['path'])
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            result=dict(error=type(exc).__name__+': '+str(exc))
        print(json.dumps(result,allow_nan=False),flush=True)


if __name__=='__main__':
    main()
