import Link from 'next/link';
import { ArchitectureDiagram } from '@/components/landing/ArchitectureDiagram';
import { HeroScene } from '@/components/landing/HeroScene';
import s from '@/components/landing/landing.module.css';

const PROBLEMS = [
  ['The answer is spread across sheets', 'A plan tags a door type, the schedule defines it and the plumbing runs on a third sheet. Checking one fact means holding all of them at once.'],
  ['A number is only as good as its source', 'A count or an area is hard to trust without the sheet, the region and the marks that produced it. A bare number gives you none of those.'],
  ['Every source speaks differently', 'A PDF may carry positioned text, a scan has only pixels, and IFC or SVG files hold exact room boundaries. One generic reader treats them all the same.'],
];
const FEATURES = [
  ['Follow the evidence', 'Every citation opens its sheet and focuses the text, box or room behind it. On placed sheets the same evidence lights up in the 3D building.'],
  ['Assemble your building', 'Place each plan with two matching corners. Elevations stand on their base line, schedules stay attached, and dashed ghosts mark plans that are missing.'],
  ['Counts that show their work', '“How many doors?” takes a short counting path: D‑FINE‑L proposes boxes for its 14 trained classes, Argon handles other objects, and Ymir counts only accepted proposals.'],
  ['Rooms and measurements', 'Rooms come from native IFC/SVG geometry or Raster2Seq polygons. Areas need a scale from the source itself; printed areas stay readings, and gaps are flagged for review.'],
  ['Check the boxes', 'When a count is uncertain, the box editor opens. Resize, delete or draw boxes and save the labels; each edit is a new revision and the model’s answer stays untouched.'],
  ['Jobs you can walk away from', 'Each question is a durable job. Watch the live trace of model calls and tools, cancel it, or close the tab and reconnect to the same job later.'],
];
const STEPS = [
  ['Import', 'Upload a PDF, image, IFC or SVG. Ymir keeps the original and its SHA-256, then prepares the PDF pages, IFC storey or SVG rooms you pick.'],
  ['Build', 'A working copy has text removed and its plan region selected. The Stoa Lens converter traces walls, doors and windows; rooms and an estimated scale complete the model.'],
  ['Assemble', 'Place sheets in one building at their levels. Ghosts show expected plans that are still missing; clicking one asks for that sheet.'],
  ['Ask', 'A job snapshots up to three sheets. Smart (DeepSeek) or Frontier (Sol) plans the tool calls, the tools return evidence, and Ymir checks it before computing the answer.'],
];
const STACK: [string, [string, string?][]][] = [
  ['Frontend', [['Next.js', 'Vercel'], ['React'], ['three.js', '2D & 3D views'], ['zustand', 'state'], ['TypeScript']]],
  ['API & state', [['FastAPI', 'NDJSON'], ['SQLite', 'jobs, receipts'], ['Pydantic', 'validation'], ['Modal', 'CPU container'], ['Python']]],
  ['Ask', [['DeepSeek / Sol', 'Smart / Frontier · OpenRouter'], ['D‑FINE‑L', 'Modal GPU'], ['Argon', 'OpenRouter'], ['VISTA', 'crops, pixels']]],
  ['Sources & rooms', [['PyMuPDF', 'PDF text'], ['IfcOpenShell', 'IFC spaces'], ['SVG', 'room outlines'], ['Raster2Seq', 'Modal GPU'], ['Shapely', 'areas']]],
  ['Model building', [['Stoa Lens', 'walls'], ['OpenCV', 'cleanup'], ['NumPy'], ['Pillow', 'images']]],
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
            <h1>Let’s make<br /><em>blueprints talk.</em></h1>
            <p>Ymir brings your sheets into one building. Ask about up to three sheets and open the drawing evidence behind the answer.</p>
            <div className={s.ctas}>
              <Link className={`${s.btn} ${s.acc}`} href="/building?id=junction-ac20">Explore the sample building</Link>
              <Link className={s.btn} href="/home">Upload a drawing</Link>
            </div>
          </div>
          <div className={s.stage}>
            <HeroScene />
            <div className={s.ask}>Which is the largest room?</div>
            <p className={s.sceneCaption}>Prebuilt sample view · drag to turn</p>
          </div>
        </div>
      </section>

      <section id="problem" className={s.section}>
        <div className={s.wrap}>
          <h2>One question can send you across several sheets.</h2>
          <div className={s.cards}>
            {PROBLEMS.map(([t, d]) => <div key={t} className={s.pcard}><h3>{t}</h3><p>{d}</p></div>)}
          </div>
          <p className={s.sourceNote}>Ymir keeps each uploaded original, its page numbers and its SHA-256 hash, so any answer can be checked against the exact file it came from.</p>
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
          <p className={s.sectionIntro}>Build prepares the 3D view; Ask answers from the selected source sheets and adds its findings as evidence overlays. The frontend runs on Vercel. One Modal CPU container runs the FastAPI app and owns the job queue, SQLite and source files. D-FINE-L and Raster2Seq run as private Modal GPU functions that scale to zero, and Smart uses DeepSeek and Frontier uses Sol on OpenRouter. Both modes use D-FINE-L for trained classes and Argon for other objects.</p>
          <div className={s.steps}>
            {STEPS.map(([t, d], i) => <div key={t} className={s.step}><span>{String(i + 1).padStart(2, '0')}</span><h3>{t}</h3><p>{d}</p></div>)}
          </div>
          <div className={s.diagram} tabIndex={0} role="region" aria-label="Ymir architecture diagram; scroll to view on small screens"><ArchitectureDiagram /></div>
          <p className={s.sourceNote}>Counts and measurements are computed by Ymir, not written by a model: it validates proposal IDs, rejects duplicate and clipped boxes, and measures polygons with Shapely. Measurements use calibration from the source; Build’s estimated scale stays with the 3D model.</p>
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
