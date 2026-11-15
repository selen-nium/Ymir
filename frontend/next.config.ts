import type { NextConfig } from 'next';

// The browser calls /api/* on this site; Next forwards it to the Ymir API (FastAPI: local uvicorn or Modal).
// Set NEXT_PUBLIC_YMIR_API_BASE instead to have the browser call the API directly (it then needs CORS).
const API = (process.env.YMIR_API_BASE || process.env.NEXT_PUBLIC_YMIR_API_BASE || 'http://localhost:8000').replace(/\/+$/, '');
if (process.env.VERCEL) {
  const publicBase = process.env.NEXT_PUBLIC_YMIR_API_BASE;
  let valid = false;
  try {
    const origin = new URL(publicBase || '');
    valid = origin.protocol === 'https:' && !origin.username && !origin.password && !origin.search && !origin.hash && !origin.pathname.replace(/\/+$/, '');
  } catch { /* the build error below names the required variable */ }
  if (!valid) {
    throw new Error('Set NEXT_PUBLIC_YMIR_API_BASE to the HTTPS Modal API origin in Vercel. Uploads and job streams connect directly to it.');
  }
}

const config: NextConfig = {
  async rewrites() {
    return [{ source: '/api/:path*', destination: `${API}/api/:path*` }];
  },
  // Links from the static frontend keep working
  async redirects() {
    const old = (source: string) => [
      { source, has: [{ type: 'query' as const, key: 'p', value: '(?<id>.*)' }], destination: '/building?id=:id', permanent: true },
      { source, has: [{ type: 'query' as const, key: 'd', value: '(?<id>.*)' }], destination: '/drawing?id=:id', permanent: true },
    ];
    return [...old('/'), ...old('/app.html'), { source: '/home.html', destination: '/home', permanent: true }, { source: '/index.html', destination: '/', permanent: true }];
  },
};

export default config;
