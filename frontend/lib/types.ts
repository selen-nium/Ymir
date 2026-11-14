// Shapes the Ymir API sends (backend/ymir): drawing payloads, answers, trace events, projects.

export type Pt = [number, number];
export type Box = [number, number, number, number];

export interface Wall {
  id: string;
  a: Pt;
  b: Pt;
  thickness_px?: number;
  confidence?: number;
  flag?: string | null;
  user?: boolean;
}

export interface Room {
  id: string;
  polygon: Pt[];
  label?: string;
  printed_area_m2?: number;
}

export interface Located {
  id: string;
  box: Box;
  text?: string;
}

export interface Drawing {
  id: string;
  title: string;
  kind?: string;
  sheet_number?: string;
  source: { dataset?: string; kind?: string; file?: string };
  image: { url: string; size: Pt };
  scale: { m_per_px: number; source: string; confidence?: number; text?: string };
  default_height_m?: number;
  plans?: { id: string; box: Box }[];
  walls: Wall[];
  rooms: Room[];
  texts: Located[];
  symbols: Located[];
  openings: Located[];
}

export interface LibraryEntry {
  id: string;
  title: string;
  thumbnail: string;
  source: { dataset: string; kind: string };
}

export interface ProjectEntry {
  id: string;
  title: string;
  thumbnail: string | null;
  sheets: number;
  source: { dataset?: string; credit?: string };
}

export interface Citation {
  n: number;
  label: string;
  box?: Box;
  room_id?: string;
  text_id?: string;
  symbol_id?: string;
  opening_id?: string;
  sheet?: string;
}

export interface ReviewBox {
  id: string;
  box: Box;
  score: number;
}

export interface Answer {
  status: 'answered' | 'abstained' | 'error';
  text: string;
  value?: number | string | null;
  unit?: string | null;
  confidence?: number;
  citations?: Citation[];
  highlight?: { rooms?: string[]; walls?: string[]; symbols?: string[]; openings?: string[] };
  focus?: Box | null;
  review?: { class: string; threshold: number; boxes: ReviewBox[] };
  needs?: { kind: string; level?: string };
  checked?: boolean;
  unchecked?: boolean;
}

export type TraceState = 'running' | 'done' | 'skipped' | 'failed' | 'waiting' | 'pending';

export type TraceEvent =
  | { type: 'node'; id: string; name: string; tool?: string; parents?: string[] }
  | { type: 'status'; id: string; state: TraceState; summary?: string; ms?: number }
  | { type: 'answer'; answer: Answer };
