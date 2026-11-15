'use client';

import Image from 'next/image';
import { useState } from 'react';
import { HeroScene } from './HeroScene';
import s from './landing.module.css';

export function BuildingPreview() {
  const [paused,setPaused]=useState(false);
  return <div className={s.stage}>
    <div className={s.stageTop}><span>AC20 / EXPLODED VIEW</span><span>MODEL STUDY — 01</span></div>
    <div className={s.axis} aria-hidden="true"><span>Z</span><i />X</div>
    <HeroScene paused={paused} />
    <span className={s.levelTag}>04 sheets<br /><b>One building</b></span>
    <div className={s.sceneCaption}><span>Prebuilt sample · drag to rotate</span><button type="button" onClick={()=>setPaused(v=>!v)} aria-pressed={paused}>{paused?'Resume rotation':'Pause rotation'} <span aria-hidden="true">{paused?'▷':'Ⅱ'}</span></button></div>
  </div>;
}

// The room polygon and label are from data/samples/ac20-eg.json, also used by
// public/hero/building.json. This illustrates source focus, not a new model answer.
const ROOM='511.2,376.8 511.2,127.2 511.2,121.9 511.2,79.2 525.6,79.2 640.8,79.2 640.8,84.5 640.8,127.2 640.8,158.4 640.8,408 640.8,412.5 640.8,448.8 525.6,448.8 511.2,448.8 511.2,444.3 511.2,408';

export function EvidencePreview() {
  const [focused,setFocused]=useState(false);
  return <div id="source-preview" className={s.evidenceDemo}>
    <div className={s.demoToolbar}><span>AC20 / GROUND FLOOR</span><span className={s.exampleTag}>Source preview</span></div>
    <div className={s.planCanvas} data-focused={focused}>
      <Image src="/hero/ac20-eg.png" width={1152} height={552} alt="AC20 ground-floor plan, with the entrance corridor in the centre" sizes="(max-width: 760px) 90vw, 650px" />
      <svg viewBox="0 0 1152 552" className={s.sourceOverlay} aria-hidden="true"><polygon points={ROOM} /><circle cx="576" cy="263" r="22" /><text x="576" y="271" textAnchor="middle">1</text></svg>
      <span className={s.planCross} aria-hidden="true">+</span>
    </div>
    <div className={s.sourceControl}>
      <div><span className={s.sourceNumber}>1</span><p><b>Flur EG Eingang</b><small>Entrance corridor · bundled sample geometry</small></p></div>
      <button type="button" aria-pressed={focused} onClick={()=>setFocused(v=>!v)}>{focused?'Reset view':'Show source'} <span aria-hidden="true">{focused?'↶':'↗'}</span></button>
    </div>
    <p className={s.demoCaption} aria-live="polite">{focused?'The sample room is highlighted on its original sheet.':'Try a citation: highlight a room on the sample drawing.'}</p>
  </div>;
}

export function PipelinePreview() {
  const [count,setCount]=useState(true);
  const steps=[['Question','Selected sheets'],['Queue','Snapshot sources'],['Route',count?'Compact counter':'General harness'],['Model + tools','Recorded operations'],['Host checks','Validate evidence'],['Answer','Citations + review']];
  return <div className={s.pipelineDemo}>
    <div className={s.pipelineToolbar}><div className={s.routeOptions} aria-label="Example question route"><button type="button" aria-pressed={count} onClick={()=>setCount(true)}>Count doors</button><button type="button" aria-pressed={!count} onClick={()=>setCount(false)}>Read the drawing</button></div><span>ILLUSTRATIVE PIPELINE</span></div>
    <ol className={s.pipeline} key={String(count)}>{steps.map(([name,detail],i)=><li key={name} className={i===2?s.routeStep:undefined} style={{animationDelay:`${i*90}ms`}}><span className={s.stepNumber}>0{i+1}</span><b>{name}</b><small>{detail}</small></li>)}</ol>
    <div className={s.pipelineBottom}><p aria-live="polite">{count?'Simple one-page counts bind a target class before the model runs.':'Other questions use the general harness and request the tools they need.'}</p><span>Answer → optional box review</span></div>
  </div>;
}
