"""Long-lived D-FINE worker: pinned EMA loaded strictly once; stdin/stdout JSONL."""
import contextlib
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback

from .dfine import CHECKPOINT_SHA256, HEADS, verified_head_policy

class Engine:
    def __init__(self,checkpoint,source):
        import torch
        from stoa_dataset_merge.dfine_v5_common import load_upstream,source_files
        checkpoint=Path(checkpoint);source=Path(source)
        policy=verified_head_policy()
        with checkpoint.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        if digest!=CHECKPOINT_SHA256:raise ValueError('Checkpoint hash mismatch')
        load_upstream(source)
        tree=hashlib.sha256(json.dumps(source_files(source),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        if tree!='ae2fe7efd6339676228c148a333163c0c5a853bcb3136ef3aae73699f3fd4988':raise ValueError('D-FINE upstream tree mismatch')
        from src.core import YAMLConfig
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():raise RuntimeError('D-FINE requires BF16 CUDA')
        config=YAMLConfig(str(source/'configs/dfine/dfine_hgnetv2_l_coco.yml'),num_classes=len(HEADS),eval_spatial_size=[960,960],remap_mscoco_category=False)
        config.yaml_cfg['HGNetv2']['pretrained']=False
        model=config.model
        saved=torch.load(checkpoint,map_location='cpu',weights_only=False)
        if saved.get('epoch')!=18:raise ValueError('Expected best checkpoint zero-based epoch 18')
        if not isinstance(saved.get('ema'),dict) or 'module' not in saved['ema']:raise ValueError('EMA state missing')
        model.load_state_dict(saved['ema']['module'],strict=True)
        self.model=model.cuda().eval();self.torch=torch
        self.identity={'checkpoint_sha256':digest,'checkpoint_epoch_zero_based':18,'weights':'EMA',
          'architecture':'D-FINE-L','resolution':960,'precision':'BF16','model_state_loaded_strictly':True,
          'head_classes':list(HEADS),'all_heads_foreground':True,'upstream_commit':'956d1709314c2c6a4df6f34de232054578a7449f','upstream_tree_sha256':tree,
          'gpu':torch.cuda.get_device_name(),'head_policy':policy}
    def predict(self,image_path,object_class,threshold):
        from PIL import Image
        from torchvision.transforms import functional as TF
        torch=self.torch
        with Image.open(image_path) as im:
            image=im.convert('RGB');w,h=image.size
            tensor=TF.to_tensor(image.resize((960,960),Image.Resampling.BILINEAR)).unsqueeze(0).cuda()
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):outputs=self.model(tensor)
        torch.cuda.synchronize()
        logits=outputs['pred_logits'].float();boxes=outputs['pred_boxes'].float()
        if logits.ndim!=3 or logits.shape[0]!=1 or logits.shape[-1]!=len(HEADS) or boxes.shape!=(1,logits.shape[1],4):raise ValueError('Unexpected model output shape/head count')
        if not torch.isfinite(logits).all() or not torch.isfinite(boxes).all():raise ValueError('Nonfinite model outputs')
        cx,cy,bw,bh=boxes[0].unbind(-1);xyxy=torch.stack((cx-bw/2,cy-bh/2,cx+bw/2,cy+bh/2),-1)
        raw=xyxy*xyxy.new_tensor([w,h,w,h]);clipped=xyxy.clamp(0,1)*xyxy.new_tensor([w,h,w,h]);scores=logits[0].sigmoid()
        all_rows=[]
        for head,label in enumerate(HEADS):
            best,indices=scores[:,head].topk(min(100,scores.shape[0]))
            for score,query in zip(best.cpu().tolist(),indices.cpu().tolist(),strict=True):
                if score<threshold:continue
                box=clipped[query].cpu().tolist();orig=raw[query].cpu().tolist()
                if box[2]<=box[0] or box[3]<=box[1]:continue
                all_rows.append({'label':label,'query_id':query,'bbox_2d':box,'raw_bbox_2d':orig,'confidence':score,
                    'clipped':box!=orig})
        selected=[{'label':r['label'],'bbox_2d':r['bbox_2d'],'confidence':r['confidence']} for r in all_rows if r['label']==object_class]
        return {'status':'complete','instances':selected,'uncertainties':[]},{**self.identity,'threshold':threshold,
            'coordinate_space':'crop pixels xyxy','requested_class':object_class,'all_class_predictions':all_rows,
            'selection':'requested head only; no parent/child summation'}

def main():
    checkpoint,source=sys.argv[1:];engine=None
    for line in sys.stdin:
        try:
            req=json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                if engine is None:engine=Engine(checkpoint,source)
                value,receipt=engine.predict(req['image_path'],req['object_class'],req['threshold'])
            print(json.dumps({'value':value,'receipt':receipt},allow_nan=False),flush=True)
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            print(json.dumps({'error':type(error).__name__+': '+str(error)}),flush=True)
if __name__=='__main__':main()
