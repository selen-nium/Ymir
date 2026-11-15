import Link from 'next/link';
import { ArchitectureDiagram } from '@/components/landing/ArchitectureDiagram';
import { HeroScene } from '@/components/landing/HeroScene';
import s from '@/components/landing/landing.module.css';

const PROBLEMS = [
  ['Spread across sheets', 'A detail can name a wall on one sheet and send you to a schedule on another. You need both to check it.'],
  ['A number needs a source', 'A count or area is hard to check without the drawing behind it. You need the sheet, the location and the evidence used.'],
  ['Different sources need different tools', 'PDFs can contain positioned text. Scans need visual inspection. Supported IFC and SVG geometry can supply explicit room boundaries.'],
];
const FEATURES = [
  ['Follow the evidence', 'Click a citation to open its sheet and focus the relevant text, box or room. Placed sheets also show the evidence in 3D.'],
  ['Assemble your building', 'Place plans at their floor level and align elevations with the building. Keep schedules and details alongside them.'],
  ['Choose the sheets', 'Ask across one to three selected sheets. Follow-up questions keep the conversation and record the scope of each answer.'],
  ['Check a count', 'Review the proposed boxes, resize them or draw missing ones. Your corrections are saved separately from the model’s answer.'],
  ['See the work', 'The trace shows Ymir tool activity as it happens. Jobs and results stay available after you leave or reload the page.'],
  ['Readings and measurements', 'Printed areas appear as readings. Measured areas and distances require source calibration; incomplete evidence is marked for review.'],
];
const STEPS = [
  ['Import', 'Upload a PDF or image, or select supported IFC/SVG geometry. Choose the pages or storey you need.'],
  ['Build', 'Check the walls and estimated scale. Verified window labels add window shapes to the model.'],
  ['Assemble', 'Place sheets in the building. Dashed shapes show where expected plans are missing.'],
  ['Ask', 'Choose up to three sheets. Ymir uses their text, pixels and available geometry, then links the answer to its evidence.'],
];
const STACK: [string, [string, string?][]][] = [
  ['Frontend', [['Next.js', 'Vercel'], ['React'], ['three.js', '3D views'], ['TypeScript']]],
  ['API & state', [['FastAPI', 'NDJSON'], ['SQLite', 'jobs, reviews, budget'], ['Pydantic', 'validation'], ['Python'], ['Modal', 'backend hosting']]],
  ['Ask tools', [['DeepSeek', 'Verda tool loop'], ['D-FINE-L', 'Modal detector'], ['Kimi', 'Verda fallback'], ['VISTA', 'pixel inspection']]],
  ['Room geometry', [['Raster2Seq', 'Modal room polygons'], ['IfcOpenShell', 'IFC spaces'], ['SVG', 'room boundaries'], ['Shapely', 'validation, areas']]],
  ['Model building', [['WallNet', 'walls'], ['OpenCV', 'line fallback'], ['NumPy', 'image processing']]],
];

export default function LandingPage() {
  return (
    <div className={s.page}>
      <header className={s.nav}>
        <div className={s.wrap}>
          <Link className="brand" href="/"><i />Ymir</Link>
          <nav><a href="#problem">Problem</a><a href="#features">Features</a><a href="#architecture">Architecture</a><a href="#stack">Stack</a></nav>
          <Link className={`${s.btn} ${s.dark} ${s.sm}`} href="/home">Open the app</Link>
        </div>
      </header>

      <section className={s.hero}>
        <div className={`${s.wrap} ${s.heroGrid}`}>
          <div className={s.heroText}>
            <h1>Ask the drawings.<br /><em>See the answer in 3D.</em></h1>
            <p>Ymir brings your sheets into one building. Ask about up to three sheets and open the drawing evidence behind the answer.</p>
            <div className={s.ctas}>
              <Link className={`${s.btn} ${s.acc}`} href="/building?id=junction-ac20">Explore the sample building</Link>
              <Link className={s.btn} href="/home">Upload a drawing</Link>
            </div>
          </div>
          <div className={s.stage}>
            <HeroScene />
            <div className={s.ask}>Which is the largest room?</div>
            <p className={s.sceneCaption}>Prebuilt sample view</p>
          </div>
        </div>
      </section>

      <section id="problem" className={s.section}>
        <div className={s.wrap}>
          <h2>One question can send you across several sheets.</h2>
          <div className={s.cards}>
            {PROBLEMS.map(([t, d]) => <div key={t} className={s.pcard}><h3>{t}</h3><p>{d}</p></div>)}
          </div>
          <p className={s.sourceNote}>Ymir keeps uploaded originals, page numbers and source hashes so you can check an answer against its source.</p>
        </div>
      </section>

      <section id="features" className={`${s.section} ${s.darkSection}`}>
        <div className={s.wrap}>
          <h2>Check the drawing behind each answer</h2>
          <div className={s.features}>
            {FEATURES.map(([t, d]) => <div key={t} className={s.fcard}><h3>{t}</h3><p>{d}</p></div>)}
          </div>
        </div>
      </section>

      <section id="architecture" className={s.section}>
        <div className={s.wrap}>
          <h2>How Ymir builds a model and answers a question</h2>
          <p className={s.sectionIntro}>Build prepares the 3D view. Ask runs Ymir against the selected source sheets and adds its findings as evidence overlays. The deployment uses Vercel for the frontend, Modal for the API and vision models, and Verda for DeepSeek and Kimi.</p>
          <div className={s.steps}>
            {STEPS.map(([t, d], i) => <div key={t} className={s.step}><span>{String(i + 1).padStart(2, '0')}</span><h3>{t}</h3><p>{d}</p></div>)}
          </div>
          <div className={s.diagram} tabIndex={0} role="region" aria-label="Ymir architecture diagram; scroll to view on small screens"><ArchitectureDiagram /></div>
          <p className={s.sourceNote}>Shapely checks and measures polygons from native geometry or Raster2Seq. Ymir measurements use source calibration; Build’s estimated scale stays with the 3D model.</p>
        </div>
      </section>

      <section id="stack" className={s.section} style={{ paddingTop: 20 }}>
        <div className={s.wrap}>
          <h2>The tools behind Ymir</h2>
          <div className={s.stack}>
            {STACK.map(([group, items]) => (
              <div key={group} className={s.scol}>
                <h3>{group}</h3>
                <ul>{items.map(([name, note]) => <li key={name}><i />{name}{note && <small>{note}</small>}</li>)}</ul>
              </div>
            ))}
          </div>
        </div>
      </section>

      <div className={s.cta}>
        <h2>See it on a real building</h2>
        <p>The sample has five sheets from an IFC model. Place the remaining floor plan and inspect the markers for missing plans.</p>
        <div className={s.ctas}>
          <Link className={`${s.btn} ${s.dark}`} href="/building?id=junction-ac20">Open the sample building</Link>
          <Link className={s.btn} href="/home">Upload your own drawing</Link>
        </div>
      </div>

      <footer className={s.footer}>
        <div className={s.wrap}>
          <Link className="brand" href="/"><i />Ymir</Link>
          <span>Built for the Stoa challenge at Junction 2026.</span>
          <span>Sample drawings: Stoa dataset, each under its own licence (AC20 model: IAI / KIT).</span>
        </div>
      </footer>
    </div>
  );
}
