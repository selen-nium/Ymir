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
  source: { dataset?: string; kind?: string; file?: string; limitations?: string[]; rendering_scope?: string; document_id?: string; source_page?: number; document_page_count?: number;
    image_registration?: { original_size: Pt; oriented_size: Pt; sheet_size: Pt; resized: boolean; original_to_sheet: number[]; sheet_to_original: number[]; exif_orientation: number } };
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
  source: { dataset: string; kind: string; credit?: string; licence?: string };
}

export interface ProjectEntry {
  id: string;
  title: string;
  thumbnail: string | null;
  sheets: number;
  source: { dataset?: string; credit?: string; licence?: string; url?: string };
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
  page_id?: string;
  evidence_id?: string;
  object_class?: string;
  proposal_id?: string;
  review_id?: number;
  provenance?: string;
  polygon?: Pt[];
  holes?: Pt[][];
  endpoints?: [Pt, Pt];
  score?: number | null;
}

export interface ReviewBox {
  id: string;
  box: Box;
  score: number | null;
  accepted?: boolean;
}

export interface CountReview { sheet?: string; page_id?: string; class: string; threshold: number; boxes: ReviewBox[]; count?: number | null; audit?: boolean }

export interface Answer {
  status: 'answered' | 'needs_review' | 'abstained' | 'error';
  text: string;
  value?: number | string | null;
  unit?: string | null;
  confidence?: number | null;
  job_id?: string;
  counts?: { sheet: string; object_class: string; count: number | null; provenance: string }[];
  rooms?: { sheet: string; room_id: string; label: string; area_m2: number | null; measurement?: {reason?: string; estimate?: boolean}; quality_flags?: string[] }[];
  measurements?: { tool: string; result: { distance_m?: number; area_m2?: number; reason?: string } }[];
  readings?: { kind: string; value: string | number | null; unit: string | null; description: string }[];
  reasons?: string[];
  reviews?: CountReview[];
  citations?: Citation[];
  highlight?: { rooms?: string[]; walls?: string[]; symbols?: string[]; openings?: string[] };
  focus?: Box | null;
  /** `audit`: a confident answer the backend picked for a spot check, reviewed like a low-confidence one. */
  review?: CountReview;
  needs?: { kind: string; level?: string };
  checked?: boolean;
  human_reviews?: {sheet: string; class: string; count: number}[];
  unchecked?: boolean;
}

export type TraceState = 'running' | 'done' | 'skipped' | 'failed' | 'waiting' | 'pending';

export type TraceEvent = (
  | { type: 'node'; id: string; name: string; tool?: string; detail?: string; parents?: string[] }
  | { type: 'status'; id: string; state: TraceState; summary?: string; ms?: number }
  | { type: 'answer'; answer: Answer }
  | { type: 'job'; job_id: string; status: string; request_id?: string }
  | { type: 'progress'; activity: Record<string, unknown> }
  | { type: 'connection'; message: string }) & { seq?: number; job_id?: string };

export interface LensStatus { ready: boolean; checks: Record<string, boolean>; budget: { cap_usd: number; remaining_usd: number; actual_usd: number; unresolved_reserved_usd: number } }
export interface LensJob { id: string; status: string; request: { request_id: string; question: string; drawing_id: string; selected_drawing_ids: string[] }; answer: Answer | null; human_reviews?: import('./session').ReviewRecord[]; created_at: string }
