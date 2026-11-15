import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => { vi.unstubAllEnvs(); vi.resetModules(); });
describe('Vercel API configuration', () => {
  it('requires a production API origin before building', async () => {
    vi.stubEnv('VERCEL', '1');vi.stubEnv('NEXT_PUBLIC_YMIR_API_BASE', '');
    await expect(import('../next.config')).rejects.toThrow('NEXT_PUBLIC_YMIR_API_BASE');
  });
  it.each(['http://localhost:8000', 'https://api.example/api', 'https://user:secret@api.example', 'https://api.example?key=value'])('rejects an invalid public origin: %s', async value => {
    vi.stubEnv('VERCEL','1');vi.stubEnv('NEXT_PUBLIC_YMIR_API_BASE',value);
    await expect(import('../next.config')).rejects.toThrow('HTTPS Modal API origin');
  });
  it('uses the public API origin for fallback rewrites', async () => {
    vi.stubEnv('VERCEL','1');vi.stubEnv('YMIR_API_BASE','');vi.stubEnv('NEXT_PUBLIC_YMIR_API_BASE','https://ymir.example/');
    const {default:config}=await import('../next.config');
    expect(await config.rewrites!()).toEqual([{source:'/api/:path*',destination:'https://ymir.example/api/:path*'}]);
  });
});
