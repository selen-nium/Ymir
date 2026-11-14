'use client';
import { useRef } from 'react';

export const DRAWING_TYPES = 'image/png,image/jpeg,image/webp';

/** A button that opens the file picker for a drawing image. A real button, so the keyboard reaches it. */
export function FilePick({ onFile, className, title, disabled, children }: {
  onFile: (file: File) => void; className?: string; title?: string; disabled?: boolean; children: React.ReactNode;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <button type="button" className={className} title={title} disabled={disabled} onClick={() => input.current?.click()}>{children}</button>
      <input ref={input} type="file" accept={DRAWING_TYPES} hidden tabIndex={-1} onChange={(e) => {
        const f = e.target.files?.[0];
        e.target.value = '';
        if (f) onFile(f);
      }} />
    </>
  );
}
