import type { Metadata } from 'next';
import { Suspense } from 'react';
import { Workspace } from '@/components/workspace/Workspace';

export const metadata: Metadata = { title: 'Building' };

export default function BuildingPage() {
  return <Suspense><Workspace kind="building" /></Suspense>;
}
