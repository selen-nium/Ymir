import Link from 'next/link';

/** The app's top bar: brand (back to the landing page), breadcrumbs, and the page's own actions on the right. */
export function TopBar({ crumbs, children }: { crumbs: React.ReactNode; children?: React.ReactNode }) {
  return (
    <header className="topbar">
      <Link className="brand" href="/" title="About Ymir"><i />Ymir</Link>
      <nav className="crumbs" aria-label="Breadcrumb">{crumbs}</nav>
      <div className="tb-actions">{children}</div>
    </header>
  );
}
