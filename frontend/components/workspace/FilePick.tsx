'use client';

export const DRAWING_TYPES = 'image/png,image/jpeg,image/webp';

/** A button that opens the file picker for a drawing image. */
export function FilePick({ onFile, className, title, children }: { onFile: (file: File) => void; className?: string; title?: string; children: React.ReactNode }) {
  return (
    <label className={className} title={title} style={{ cursor: 'pointer' }}>
      {children}
      <input type="file" accept={DRAWING_TYPES} hidden onChange={(e) => {
        const f = e.target.files?.[0];
        e.target.value = '';
        if (f) onFile(f);
      }} />
    </label>
  );
}
