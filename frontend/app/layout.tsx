import type { Metadata, Viewport } from 'next';
import { Inter, JetBrains_Mono } from 'next/font/google';
import { Toaster } from '@/components/Toaster';
import './globals.css';

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' });
const mono = JetBrains_Mono({ subsets: ['latin'], weight: '500', variable: '--font-mono' });

export const metadata: Metadata = {
  title: { default: 'Ymir · Ask your construction drawings', template: '%s · Ymir' },
  description: 'Ymir answers engineers’ questions about construction drawings, with the evidence marked in 2D and in a 3D building assembled from every sheet.',
};
export const viewport: Viewport = { themeColor: '#f4f1ea' };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body>
        {children}
        <Toaster />
      </body>
    </html>
  );
}
