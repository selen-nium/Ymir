import {expect,test} from 'vitest';
import {applyReviews,answerText,answerNotes} from '@/lib/answer-presentation';
import {createSession,type ReviewRecord} from '@/lib/session';
import {loadThread,saveThread,type KeyValueStorage} from '@/lib/persist';
import type {Answer,Drawing} from '@/lib/types';

const machine: Answer={status:'needs_review',job_id:'job',text:'2 window · host-derived count.',
  counts:[{sheet:'a',object_class:'window',count:2,provenance:'host_derived'}],
  reviews:[{sheet:'a',class:'window',threshold:0,boxes:[{id:'p1',box:[10,10,30,30],score:null,accepted:true},{id:'p2',box:[40,10,60,30],score:.7,accepted:true}]}],
  citations:[{n:1,label:'window',sheet:'a',evidence_id:'p1',box:[10,10,30,30]},{n:2,label:'window',sheet:'a',evidence_id:'p2',box:[40,10,60,30]},
    {n:3,label:'Other sheet',sheet:'b',box:[10,10,30,30]}]};
const saved: ReviewRecord={id:1,schema:'drawing-qa-review-v1',job_id:'job',sheet_id:'a',drawing:{id:'a',title:'A',image_size:[100,100]},question:'Count windows',class:'window',user_count:2,
  model:{value:2,threshold:0,scores:{p1:null,p2:.7},boxes:{p1:[10,10,30,30],p2:[40,10,60,30]}},reviewed_at:'2026-11-15',
  objects:[{id:'p1',class:'window',bbox_xyxy:[12,12,32,32],subtype:'',status:'accepted',proposal_id:'p1'},
    {id:'p2',class:'window',bbox_xyxy:[40,10,60,30],subtype:'',status:'rejected',proposal_id:'p2'},
    {id:'u1',class:'window',bbox_xyxy:[65,65,85,85],subtype:'',status:'accepted'}]};

test('verified boxes replace model boxes, keep other sheets and never mutate the machine result',()=>{
  const before=JSON.stringify(machine);const answer=applyReviews(machine,[saved]);
  expect(answer.status).toBe('answered');expect(answer.text).toBe('I found 2 windows.');
  expect(answer.citations?.filter(c=>c.sheet==='a')).toHaveLength(2);
  expect(answer.citations?.filter(c=>c.sheet==='a').every(c=>c.label==='Window · human-verified label')).toBe(true);
  expect(answer.citations?.some(c=>c.proposal_id==='p2')).toBe(false);
  expect(answer.citations?.find(c=>c.proposal_id==='p1')?.box).toEqual([12,12,32,32]);
  expect(answer.citations?.filter(c=>c.sheet==='b')).toHaveLength(1);
  expect(JSON.stringify(machine)).toBe(before);
  const revision={...saved,id:2,user_count:0,objects:saved.objects.map(o=>({...o,status:'rejected' as const}))};
  const revised=applyReviews(machine,[saved,revision]);
  expect(revised.citations?.filter(c=>c.sheet==='a')).toHaveLength(0);
  expect(revised.text).toBe('I found 0 windows.');
});

test('reopening restores resized, rejected and added boxes with original detector attribution',()=>{
  const session=createSession();session.state.d={id:'a',title:'A',source:{},image:{url:'',size:[100,100]},scale:{m_per_px:.1,source:'estimate'},walls:[],rooms:[],texts:[],symbols:[],openings:[]} satisfies Drawing;
  session.startReview({...applyReviews(machine,[saved]),review:machine.reviews![0]},'Count windows',saved);
  expect(session.reviewStats().count).toBe(2);
  expect(session.state.review?.items.find(i=>i.id==='p1')).toMatchObject({box:[12,12,32,32],orig:[10,10,30,30],touched:true});
  expect(session.state.review?.items.find(i=>i.id==='p2')?.counted).toBe(false);
  expect(session.addBox([1,60,20,80])?.id).toBe('u2');
  const result=session.finishReview()!;
  expect(result.record.revision_of).toBe(1);
  expect(result.record.model.boxes.p1).toEqual([10,10,30,30]);
  expect(result.answer.citations?.filter(c=>c.sheet==='a')).toHaveLength(3);
});

test('reviews stay separate for each sheet and class, and non-count uncertainty stays visible',()=>{
  const multi={...machine,reviews:[...machine.reviews!,{...machine.reviews![0],sheet:'b'}]};
  expect(applyReviews(multi,[saved]).status).toBe('needs_review');
  const mixed={...machine,rooms:[{sheet:'a',room_id:'r',label:'Room',area_m2:null}]};
  expect(applyReviews(mixed,[saved]).status).toBe('needs_review');
});

test('old count receipts read naturally and technical notes become short caveats',()=>{
  expect(answerText(machine)).toBe('I found 2 windows.');
  const notes=answerNotes({...machine,reasons:['Accepted: proposal_abc at x 10 / y 20 in view-abc.','Scope limited to this single provided page.','A tool failed before the remaining objects were checked.']});
  expect(notes).toEqual(['Check the boxes to finish the count.','A tool could not finish. Check the affected evidence.']);
  expect(answerText({...machine,counts:[],text:'Answer grounded in the selected sheets.',reasons:['Answer: the largest room is the living room (candidate r2s-abc), on the right.']})).toBe('the largest room is the living room, on the right.');
});

test('browser storage retains saved records and the untouched machine answer',()=>{
  const map=new Map<string,string>();const storage:KeyValueStorage={getItem:k=>map.get(k)??null,setItem:(k,v)=>{map.set(k,v)},removeItem:k=>{map.delete(k)}};
  saveThread('p',[{sheet:'a',q:'Count windows',modelAnswer:machine,answer:applyReviews(machine,[saved]),records:[saved],record:saved}],storage);
  expect(loadThread('p',storage)[0]).toMatchObject({modelAnswer:machine,records:[saved],record:saved});
});
