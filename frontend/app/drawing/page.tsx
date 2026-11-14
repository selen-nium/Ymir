import type { Metadata } from 'next';
import { Suspense } from 'react';
import { Workspace } from '@/components/workspace/Workspace';

export const metadata: Metadata = { title: 'Drawing' };

export default function DrawingPage() {
  return <Suspense><Workspace kind="drawing" /></Suspense>;
}
