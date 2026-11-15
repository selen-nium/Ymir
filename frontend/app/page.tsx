import Image from 'next/image';
import Link from 'next/link';
import { BuildingPreview, EvidencePreview, PipelinePreview } from '@/components/landing/LandingDemos';
import s from '@/components/landing/landing.module.css';

const TECHNOLOGIES = [
  ['nextdotjs', 'Next.js'], ['react', 'React'], ['threedotjs', 'Three.js'],
  ['python', 'Python'], ['fastapi', 'FastAPI'], ['sqlite', 'SQLite'],
];

export default function LandingPage() {
  return (
    <div className={s.page}>
      <a className={s.skipLink} href="#main">Skip to content</a>
      <header className={s.nav}>
        <div className={s.wrap}>
          <Link className={`brand ${s.brand}`} href="/" aria-label="Ymir home"><i />Ymir<span>DRAWINGS, CONNECTED.</span></Link>
          <nav aria-label="Main navigation"><a href="#workspace">Workspace</a><a href="#evidence">Evidence</a><a href="#pipeline">How it works</a></nav>
          <Link className={`${s.btn} ${s.dark} ${s.sm}`} href="/home">Open Ymir <span aria-hidden="true">↗</span></Link>
        </div>
      </header>

      <main id="main">
        <section className={s.hero} aria-labelledby="hero-title">
          <div className={`${s.wrap} ${s.heroGrid}`}>
            <div className={s.heroText}>
              <div className={s.eyebrow}><span className={s.liveDot} />From sheets to understanding</div>
              <h1 id="hero-title">Let’s make<br />blueprints<br /><em>talk.</em></h1>
              <p>Ymir brings your sheets into one building.</p>
              <div className={s.ctas}>
                <Link className={`${s.btn} ${s.acc}`} href="/building?id=ac20">Explore a building <span aria-hidden="true">↗</span></Link>
                <Link className={s.textLink} href="/home">Bring your own drawing <span aria-hidden="true">→</span></Link>
              </div>
              <div className={s.heroNote}><span aria-hidden="true">⌘</span> A workspace for construction drawings.</div>
            </div>
            <BuildingPreview />
          </div>
          <div className={`${s.wrap} ${s.heroFoot}`}><span>DRAWING → BUILDING → EVIDENCE</span><a href="#workspace">Take a closer look <span aria-hidden="true">↓</span></a></div>
        </section>

        <section className={`${s.wrap} ${s.formats}`} aria-label="Supported source formats">
          <p>Your drawings.<br /><b>Their original detail.</b></p>
          <div className={s.formatList}>{[['PDF', 'Selected pages'], ['IMG', 'Images & scans'], ['IFC', 'Storey spaces'], ['SVG', 'Room outlines']].map(([format, detail])=>(
            <div className={s.format} key={format}><span className={s.fileIcon} aria-hidden="true">{format}</span><span>{format==='IMG'?'Images':format}<small>{detail}</small></span></div>
          ))}</div>
        </section>

        <section id="workspace" className={s.workspaceSection}>
          <div className={s.wrap}>
            <div className={s.sectionHead}>
              <div><span className={s.eyebrow}>01 / The workspace</span><h2>The whole building.<br /><em>In the same conversation.</em></h2></div>
              <p>Place your sheets. Explore in 2D and 3D.<br />Ask from the source drawings.</p>
            </div>
            <figure className={s.productFrame}>
              <div className={s.productBar}><span className={s.windowDots} aria-hidden="true"><i /><i /><i /></span><span>AC20 / Sample building</span><span className={s.viewBadge}>2D + 3D</span></div>
              <Image src="/landing/workspace.png" width={1068} height={884} sizes="(max-width: 1200px) 94vw, 1232px" alt="Actual Ymir workspace showing the AC20 ground-floor drawing beside its assembled 3D building" className={s.workspaceImage} />
              <figcaption>Actual workspace · bundled AC20 sample</figcaption>
            </figure>
            <div className={s.workspaceNotes}>
              <div><span>01</span><p><b>Bring the sheets together.</b> Match corners to place each plan.</p></div>
              <div><span>02</span><p><b>Keep the gaps visible.</b> Missing plans appear as dashed outlines.</p></div>
              <div><span>03</span><p><b>Stay close to the source.</b> Open the drawing behind a citation.</p></div>
            </div>
          </div>
        </section>

        <section id="evidence" className={`${s.wrap} ${s.evidenceSection}`}>
          <div className={s.evidenceCopy}>
            <span className={s.eyebrow}>02 / Evidence you can see</span>
            <h2>Don’t just get<br />an answer.<br /><em>Find it.</em></h2>
            <p>Citations take you to the source sheet and the region behind the answer.</p>
            <a className={s.textLink} href="#source-preview">Try the source highlight <span aria-hidden="true">↘</span></a>
            <div className={s.marginNote}><span aria-hidden="true">+</span><p>Counts come from accepted boxes.<br />Measurements need source-bound scale.</p></div>
          </div>
          <EvidencePreview />
        </section>

        <section id="pipeline" className={s.pipelineSection}>
          <div className={s.wrap}>
            <div className={s.sectionHead}>
              <div><span className={s.eyebrow}>03 / Follow the work</span><h2>A question goes in.<br /><em>The steps stay visible.</em></h2></div>
              <p>See the route, model calls, tools and host checks.<br />Reconnect to the same job after a reload.</p>
            </div>
            <PipelinePreview />
            <div className={s.engineNotes}><span><b>DeepSeek</b> chooses tools</span><span><b>D-FINE-L / Kimi</b> localize objects</span><span><b>Native geometry / Raster2Seq</b> provide room outlines</span></div>
          </div>
        </section>

        <section className={`${s.wrap} ${s.reviewSection}`}>
          <div className={s.reviewGraphic} aria-hidden="true">
            <div className={s.reviewGrid} /><span className={s.reviewBox}><i /><i /><i /><i /><b>Check the boundary</b></span>
            <span className={s.reviewCursor}><svg viewBox="0 0 24 28"><path d="M3 2v21l6-6 4 9 4-2-4-8h9Z" /></svg></span>
            <div className={s.reviewStamp}><span>✓</span> Your check stays on record</div>
          </div>
          <div className={s.reviewCopy}><span className={s.eyebrow}>04 / A human has the last look</span><h2>Some boxes<br />need your eye.</h2><p>Keep, resize, remove or draw a box. Save your labels as a review, with the model’s original answer preserved.</p><span className={s.smallNote}>Box review is available for count evidence.</span></div>
        </section>

        <section className={`${s.wrap} ${s.technology}`} aria-labelledby="technology-title">
          <div><h2 id="technology-title">Under the hood.</h2><p>Built with tools we can name.</p></div>
          <div className={s.logos}>{TECHNOLOGIES.map(([file,name])=><div key={file}><Image src={`/landing/logos/${file}.svg`} width={28} height={28} alt="" /><span>{name}</span></div>)}</div>
        </section>

        <section className={s.cta}>
          <div className={s.wrap}><span className={s.eyebrow}>Start with a real drawing</span><h2>Your next question<br />is already on the plan.</h2><Link className={`${s.btn} ${s.dark}`} href="/building?id=ac20">Open the sample building <span aria-hidden="true">↗</span></Link><div className={s.ctaLine} aria-hidden="true" /></div>
        </section>
      </main>

      <footer className={s.footer}><div className={s.wrap}><Link className="brand" href="/"><i />Ymir</Link><span>Built for the Stoa challenge · Junction 2026</span><small>AC20 sample: IAI / KIT · Technology marks identify dependencies.</small><a href="#main">Back to top ↑</a></div></footer>
    </div>
  );
}
