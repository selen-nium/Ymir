import {expect,test} from 'vitest';
import {elapsedLabel,progressLabel} from '@/lib/job-progress';

test('queued, running, tool work and final checks have different live labels',()=>{
  expect(progressLabel({type:'job',job_id:'j',status:'queued'})).toBe('Queued for a worker…');
  expect(progressLabel({type:'job',job_id:'j',status:'running'})).toBe('Reading selected drawings…');
  expect(progressLabel({type:'progress',activity:{operation:'tool',phase:'started',tool:'predict_room_polygons'}})).toBe('Finding room outlines…');
  expect(progressLabel({type:'progress',activity:{operation:'response',phase:'started',final:true}})).toBe('Checking the answer…');
  expect(progressLabel({type:'node',id:'n',name:'Raw model detail'})).toBeNull();
  expect(elapsedLabel(1000,65000)).toBe('1m 4s elapsed');
});
