import type { NextConfig } from 'next';

// The browser calls /api/* on this site; Next forwards it to the Ymir API (FastAPI: local uvicorn or Modal).
// Set NEXT_PUBLIC_YMIR_API_BASE instead to have the browser call the API directly (it then needs CORS).
const API = (process.env.YMIR_API_BASE || 'http://localhost:8000').replace(/\/+$/, '');
if (process.env.VERCEL && !process.env.YMIR_API_BASE && !process.env.NEXT_PUBLIC_YMIR_API_BASE) {
  throw new Error('Set YMIR_API_BASE (the Modal API URL) in the Vercel project settings.');
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
