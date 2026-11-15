// Project Lens's durable activity into an execution sequence. Recorded events stay
// unchanged, including older jobs whose node events all pointed at one root.
import type {TraceEvent,TraceState} from './types';
import {orchestratorLabel} from './routing';
import {productName, answerText, requestStatus} from './answer-presentation';

type NodeEvent=Extract<TraceEvent,{type:'node'}>;
type Activity=Record<string,unknown>;
const record=(v:unknown):Activity=>v && typeof v==='object'?v as Activity:{};
const str=(v:unknown)=>typeof v==='string'?v:'';
const title=(name:string)=>name.replaceAll('_',' ').replace(/^./,c=>c.toUpperCase());

export function createLensTraceSequence(){
  let lens=false,last:string|undefined,response=0,anonymous=0,review=0,jobStatus='';
  const nodes=new Map<string,NodeEvent>();
  const active=new Map<string,string>();
  const aliases=new Map<string,string>();
  const pendingTools=new Map<string,string>();
  const toolKinds=new Map<string,string>();
  function node(id:string,name:string,tool:string,detail?:string):TraceEvent[]{
    const old=nodes.get(id);
    const event:NodeEvent={type:'node',id,name,tool,detail,parents:old?.parents ?? (last?[last]:[])};
    nodes.set(id,event);
    if(!old)last=id;
    return [event];
  }
  function status(id:string,state:TraceState,summary:string):TraceEvent[]{
    if(state==='running')active.set(id,summary);else active.delete(id);
    return [{type:'status',id,state,summary:requestStatus(summary)}];
  }
  function finishActive(state:TraceState,summary:string):TraceEvent[]{
    return [...active.keys()].flatMap(id=>status(id,state,summary));
  }
  return {
    get sequential(){return lens;},
    apply(event:TraceEvent):TraceEvent[]{
      if(event.type==='stage'){
        lens=true;
        const id=`stage-${event.stage}`,name=event.stage==='checks'?'Host checks':title(event.stage);
        return [...node(id,name,'Ymir',event.detail),...status(id,event.state,event.summary)];
      }
      if(event.type==='job'){
        lens=true;jobStatus=event.status;
        if(['failed','interrupted','cancelled'].includes(event.status))return finishActive(event.status==='cancelled'?'skipped':'failed',title(event.status));
        return [];
      }
      if(event.type==='progress'){
        lens=true;const a=event.activity;
        if(a.operation==='response'){
          if(typeof a.response_index==='number')response=a.response_index;
          const id=`response-${response+1}`;
          if(a.phase==='started')return [...node(id,`Ymir response ${response+1}`,str(a.model)?orchestratorLabel(str(a.model)):'DeepSeek',str(a.model)||undefined),...status(id,'running',a.final?'Finalize from recorded evidence':'Read evidence and choose tools')];
          return status(id,a.phase==='failed'?'failed':'done',str(a.reason)||str(a.error_type)||(a.final?'Final response received':'Response received'));
        }
        if(a.operation==='tool'){
          const index=typeof a.response_index==='number'?a.response_index:response;
          const call=str(a.call_id),name=str(a.tool),key=`${index}:${call || name}`;
          let id=pendingTools.get(key);
          if(a.phase==='started' || !id){
            id=call?`tool-${index+1}-${call}`:`tool-${index+1}-anonymous-${++anonymous}`;
            pendingTools.set(key,id);
          }
          const result=record(a.result),route=record(a.routing ?? result.routing);
          const backend=str(route.backend),model=str(a.model ?? result.model);
          const kind=str(route.requested_class)||str(route.canonical_class)||toolKinds.get(id)||'';
          if(kind)toolKinds.set(id,kind);
          const detector=backend==='dfine'?'D-FINE-L':backend==='argon'?'Argon':backend==='kimi'?'Kimi':model;
          const events=node(id,title(name),name==='detect_objects'?(detector||nodes.get(id)?.tool||'Detector'):name==='inspect' || name==='read_pixels'?'VISTA':name==='read_pdf_text'?'Native PDF':name==='predict_room_polygons'?'Raster2Seq':'Geometry',model||nodes.get(id)?.detail);
          const summary=[kind,str(result.error_type)||str(result.status)].filter(Boolean).join(' · ') || (a.phase==='started'?'Running':'Complete');
          return [...events,...status(id,a.phase==='started'?'running':result.error_type?'failed':'done',summary)];
        }
        return [];
      }
      if(!lens)return event.type==='node' ? [{...event,name:productName(event.name),tool:event.tool?productName(event.tool):undefined}] : [event];
      if(event.type==='answer'){
        const failed=event.answer.status==='error',cancelled=jobStatus==='cancelled';
        return [...finishActive(cancelled?'skipped':'failed','Operation ended without a recorded result'),
          ...node('lens-answer',failed?'Job stopped':event.answer.status==='needs_review'?'Evidence needs review':'Answer','Ymir'),
          ...status('lens-answer',cancelled?'skipped':failed?'failed':'done',answerText(event.answer))];
      }
      // Human review follows the machine answer, including repeated reviews of
      // different sheet/class groups.
      if(event.type==='node' && event.id==='you'){
        const id=`review-${++review}`;aliases.set('you',id);
        return node(id,event.name,event.tool ?? 'You');
      }
      if(event.type==='node' && event.id==='ans'){
        const id=`review-result-${review}`;aliases.set('ans',id);
        return node(id,'Review result',event.tool ?? 'You');
      }
      if(event.type==='status' && aliases.has(event.id))return status(aliases.get(event.id)!,event.state,event.summary ?? '');
      // Lens node/status events duplicate the activity receipts. The activity
      // stream supplies response identity and execution order on live and replay.
      return [];
    },
  };
}
