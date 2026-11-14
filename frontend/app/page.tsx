import Link from 'next/link';
import { ArchitectureDiagram } from '@/components/landing/ArchitectureDiagram';
import { HeroScene } from '@/components/landing/HeroScene';
import s from '@/components/landing/landing.module.css';

const PROBLEMS = [
  ['Spread across sheets', 'How long are the footings under the outside walls? That takes the foundation plan, the floor plan and a schedule, drawn by three disciplines.'],
  ['Answers without evidence', 'A number without a source is a liability. Engineers need to see which room, which wall and which sheet it came from, and how sure the model is.'],
  ['Every set is different', 'Scanned archives, CAD exports and IFC models; Finnish, German and Danish labels; metric and imperial. One fixed pipeline doesn’t fit them all.'],
];
const FEATURES = [
  ['Cited in 2D and 3D', 'Numbered citations mark the rooms, walls and symbols behind an answer, on the sheet and on the model. Click one to fly there.'],
  ['One building from many sheets', 'Plans lie flat at their level, elevations stand on their facade, schedules link to what they describe. Two clicks line up a new sheet.'],
  ['Honest about gaps', 'Missing sheets show as ghosts in the model. Ymir abstains instead of guessing, and points at the sheet it would need.'],
  ['A person in the loop', 'You confirm the scale and the doubtful walls. When a count is unsure you check its boxes, and your corrections become training labels.'],
  ['See how it answered', 'A live trace shows the planner, the tools it called and the verifier’s check as they run, so a wrong answer is easy to diagnose.'],
  ['Any drawing, any language', 'A scan, a CAD export or an IFC-rendered sheet: Ymir sorts it by kind, reads its labels and asks you when it isn’t sure.'],
];
const STEPS = [
  ['Upload', 'A sheet of any kind, or a whole set.'],
  ['Build', 'Walls, rooms, text and scale are read from the image. You confirm what the model isn’t sure about.'],
  ['Assemble', 'Sheets take their place in one building. What’s missing becomes a ghost.'],
  ['Ask', 'The planner routes the question to tools, the verifier checks, the answer cites its sources.'],
];
const STACK: [string, [string, string?][]][] = [
  ['Frontend', [['Next.js', 'Vercel'], ['React'], ['three.js', '3D model'], ['TypeScript']]],
  ['API', [['Python 3.12'], ['FastAPI', 'streaming'], ['Pydantic'], ['Modal', 'scale to zero']]],
  ['Vision', [['PyTorch', 'WallNet'], ['YOLO11', 'symbols'], ['RapidOCR', 'ONNX'], ['OpenCV']]],
  ['Geometry · BIM', [['Shapely', 'rooms, areas'], ['IfcOpenShell', 'IFC → sheets'], ['NumPy'], ['Claude', 'vision fallback']]],
  ['Data · ops', [['SQLite', 'reviews'], ['Git LFS', 'weights'], ['GitHub Actions', 'CI, deploy'], ['Vitest · pytest']]],
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
            <p>Ymir assembles your sheets into one building, answers in plain language, and points to the evidence.</p>
            <div className={s.ctas}>
              <Link className={`${s.btn} ${s.acc}`} href="/building?id=ac20">Explore the sample building</Link>
              <Link className={s.btn} href="/home">Upload a drawing</Link>
            </div>
          </div>
          <div className={s.stage}>
            <HeroScene />
            <div className={s.ask}>Which is the largest room?</div>
          </div>
        </div>
      </section>

      <section id="problem" className={s.section}>
        <div className={s.wrap}>
          <span className={s.kicker}>The problem</span>
          <h2>The answers are in the drawings. Finding them takes hours.</h2>
          <div className={s.cards}>
            {PROBLEMS.map(([t, d]) => <div key={t} className={s.pcard}><h3>{t}</h3><p>{d}</p></div>)}
          </div>
          <div className={s.stats}>
            <div><b>14,000</b><span>construction drawings</span></div>
            <div><b>~30</b><span>languages on the sheets</span></div>
            <div><b>120,000</b><span>quantity-takeoff questions</span></div>
            <small>In the Stoa challenge dataset Ymir is built and tested on.</small>
          </div>
        </div>
      </section>

      <section id="features" className={`${s.section} ${s.darkSection}`}>
        <div className={s.wrap}>
          <span className={s.kicker}>What Ymir does</span>
          <h2>Every answer comes with its evidence. Every gap is shown.</h2>
          <div className={s.features}>
            {FEATURES.map(([t, d], i) => <div key={t} className={s.fcard}><span className={s.fnum}>{String(i + 1).padStart(2, '0')}</span><h3>{t}</h3><p>{d}</p></div>)}
          </div>
        </div>
      </section>

      <section id="architecture" className={s.section}>
        <div className={s.wrap}>
          <span className={s.kicker}>How it works</span>
          <h2>From a scanned sheet to a cited answer</h2>
          <div className={s.steps}>
            {STEPS.map(([t, d], i) => <div key={t} className={s.step}><span>{String(i + 1).padStart(2, '0')}</span><h3>{t}</h3><p>{d}</p></div>)}
          </div>
          <div className={s.diagram}><ArchitectureDiagram /></div>
        </div>
      </section>

      <section id="stack" className={s.section} style={{ paddingTop: 20 }}>
        <div className={s.wrap}>
          <span className={s.kicker}>Tech stack</span>
          <h2>Small, plain and quick to change</h2>
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
        <p>Five sheets from an IFC model, one waiting to be placed, and a plumbing plan that isn’t there.</p>
        <div className={s.ctas}>
          <Link className={`${s.btn} ${s.dark}`} href="/building?id=ac20">Open the sample building</Link>
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
