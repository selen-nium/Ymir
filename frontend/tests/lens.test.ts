import {expect,test} from 'vitest';
import {createSession,needsReview} from '@/lib/session';
import {saveThread,loadThread,type KeyValueStorage} from '@/lib/persist';
import type {Answer,Drawing} from '@/lib/types';

const drawing: Drawing={id:'sheet-a',title:'Source',source:{},image:{url:'source.png',size:[100,100]},scale:{m_per_px:.1,source:'estimate'},walls:[],rooms:[],texts:[],symbols:[],openings:[]};
const answer: Answer={status:'needs_review',job_id:'job-1',text:'Incomplete',confidence:null,review:{sheet:'sheet-a',class:'door',threshold:0,boxes:[{id:'p1',box:[10,10,40,40],score:null,accepted:false}]},
  citations:[{n:1,label:'A',sheet:'sheet-a',box:[10,10,40,40],polygon:[[10,10],[40,10],[40,40]],holes:[[[20,20],[25,20],[25,25]]]}, {n:2,label:'B',sheet:'sheet-b',box:[60,60,90,90]}]};

test('Lens review is explicit, null scores stay null and citations are sheet-scoped',()=>{
  const session=createSession();session.state.d=drawing;
  expect(needsReview(answer)).toBe(true);expect(needsReview({...answer,status:'answered'})).toBe(false);
  session.setAnswer(answer);
  expect(session.state.answer?.boxes).toHaveLength(1);
  expect(session.state.answer?.regions?.[0].holes).toHaveLength(1);
  expect(session.citationBox(answer.citations![1])).toBeNull();
  session.startReview(answer,'Count doors');expect(session.state.review?.items[0].score).toBeNull();
  session.toggleBox('p1');const review=session.finishReview()!;
  expect(review.record.job_id).toBe('job-1');expect(review.record.sheet_id).toBe('sheet-a');
  expect(review.answer.status).toBe('answered');expect(review.answer.human_reviews?.[0].count).toBe(1);
  expect(answer.review?.boxes[0].accepted).toBe(false);
});

test('pending jobs survive browser persistence alongside historical answers',()=>{
  const data=new Map<string,string>();const storage: KeyValueStorage={getItem:k=>data.get(k)??null,setItem:(k,v)=>{data.set(k,v)},removeItem:k=>{data.delete(k)}};
  saveThread('p',[{q:'Old answer',sheet:'sheet-a',answer:{status:'answered',text:'Old'}},{id:'r',q:'New question',sheet:'sheet-a',sheets:['sheet-a','sheet-b'],job_id:'j',request_id:'r',status:'pending'}],storage);
  expect(loadThread('p',storage)[1]).toMatchObject({status:'pending',job_id:'j',request_id:'r',sheets:['sheet-a','sheet-b']});
  expect(loadThread('p',storage)[0].answer?.text).toBe('Old');
});
