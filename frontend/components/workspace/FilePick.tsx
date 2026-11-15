'use client';
import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { selectImport, uploadDocument, type DocumentInventory, type ImportSelection } from '@/lib/api';

export const DRAWING_TYPES = 'image/png,image/jpeg,image/webp,application/pdf,.ifc,.svg';

/** Native document import keeps page/geometry choices explicit before Build. */
export function FilePick({ onFile, className, title, disabled, children }: {
  onFile: (file: File) => void; className?: string; title?: string; disabled?: boolean; children: React.ReactNode;
}) {
  const input = useRef<HTMLInputElement>(null), dialog = useRef<HTMLDialogElement>(null);
  const [pending,setPending]=useState<{file:File;doc:DocumentInventory}|null>(null);
  const [selected,setSelected]=useState<string[]>([]), [busy,setBusy]=useState(false), [error,setError]=useState('');
  const [confirmScale,setConfirmScale]=useState(false);
  const titleId=useId();
  useEffect(()=>{if(pending)dialog.current?.showModal();},[pending]);
  const choose = async (file: File) => {
    setError('');
    if(!/\.(pdf|svg|ifc)$/i.test(file.name)){onFile(file);return;}
    setBusy(true);
    try {
      const doc=await uploadDocument(file);
      setPending({file,doc});setSelected(doc.format==='pdf'?doc.pages.map(p=>String(p.number)):[]);setConfirmScale(false);
    } catch(e){setError((e as Error).message);}
    finally{setBusy(false);}
  };
  const prepare = (form: HTMLFormElement) => {
    if(!pending || !selected.length)return;
    const {doc,file}=pending;const selection: ImportSelection = {};
    if(doc.format==='pdf')selection.pages=selected.map(Number);
    else if(doc.format==='ifc')selection.options={storey_id:selected[0]};
    else {
      selection.options={element_ids:selected};
      if(confirmScale){
        const data=new FormData(form), n=(k:string)=>Number(data.get(k));
        selection.options.svg_scale={p0:[n('x0'),n('y0')],p1:[n('x1'),n('y1')],length:n('length'),unit:String(data.get('unit')),reading:String(data.get('reading')),reviewer:String(data.get('reviewer'))};
      }
    }
    selectImport(file,doc,selection);dialog.current?.close();setPending(null);onFile(file);
  };
  const options=pending?.doc.format==='pdf'?pending.doc.pages.map(p=>({id:String(p.number),label:`Page ${p.number}${p.rotation?` · rotated ${p.rotation}°`:''}${p.native_text?' · native text':''}`})):pending?.doc.selectors ?? [];
  return <>
    <button type="button" className={className} title={title} disabled={disabled || busy} onClick={() => input.current?.click()}>{busy?'Reading document…':children}</button>
    <input ref={input} type="file" accept={DRAWING_TYPES} hidden tabIndex={-1} onChange={(e) => {
      const f=e.target.files?.[0];e.target.value='';if(f)void choose(f);
    }} />
    {error && <span role="alert" className="import-error">{error}</span>}
    {pending && createPortal(<dialog ref={dialog} className="import-dialog" aria-labelledby={titleId} onCancel={()=>setPending(null)}>
      <form onSubmit={e=>{e.preventDefault();e.stopPropagation();prepare(e.currentTarget);}}>
        <h2 id={titleId}>Import {pending.doc.filename}</h2>
        <p>{pending?.doc.format==='pdf'?'Select pages to add as sheets. Each question can use up to three sheets.':pending?.doc.format==='ifc'?'Select one IFC storey.':'Select the elements that represent rooms.'}</p>
        {pending?.doc.limitations.map(t=><p key={t}>{t}</p>)}
        <fieldset className="import-options"><legend>{pending?.doc.format==='pdf'?'Pages':pending?.doc.format==='ifc'?'Storey':'Room elements'}</legend>
          {options.map(o=><label key={o.id}><input type={pending?.doc.format==='ifc'?'radio':'checkbox'} name="selector" checked={selected.includes(o.id)} onChange={e=>setSelected(pending?.doc.format==='ifc'?[o.id]:e.target.checked?[...selected,o.id]:selected.filter(s=>s!==o.id))}/>{o.label}</label>)}
          {!options.length && <p>No selectable geometry was found in this source.</p>}
        </fieldset>
        {pending?.doc.format==='svg' && <>
          <label><input type="checkbox" checked={confirmScale} onChange={e=>setConfirmScale(e.target.checked)}/>Confirm a visible length for measurements</label>
          {confirmScale && <fieldset className="scale-fields"><legend>Length in SVG root coordinates</legend>
            {['x0','y0','x1','y1'].map(k=><label key={k}>{k}<input name={k} type="number" step="any" required/></label>)}
            <label>Length<input name="length" type="number" step="any" min="0.000001" required/></label>
            <label>Unit<select name="unit">{['m','cm','mm','ft','in'].map(u=><option key={u}>{u}</option>)}</select></label>
            <label>Visible reading<input name="reading" required maxLength={1000}/></label>
            <label>Reviewer<input name="reviewer" required maxLength={120}/></label>
          </fieldset>}
        </>}
        <div className="row"><button type="button" className="btn" onClick={()=>{dialog.current?.close();setPending(null);}}>Cancel</button><button type="submit" className="btn acc" disabled={!selected.length}>Prepare selected sheets</button></div>
      </form>
    </dialog>,document.body)}
  </>;
}
