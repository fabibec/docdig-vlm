// Mirrors backend/api/schemas.py + enums.py (JSON field names). Keep in sync by hand;
// the backend's OpenAPI (/openapi.json) is the reference.

export type DocStatus = 'queued' | 'processing' | 'review' | 'done' | 'error';
export type ArtifactState = 'raw' | 'cropped' | 'processed';
export type ColumnKey =
  | 'ring_no' | 'species' | 'sex' | 'age' | 'date' | 'time'
  | 'wing_length_mm' | 'weight_g' | 'additional_remark';
export type FlagSource = 'llm' | 'rule' | 'user';
export type Severity = 'error' | 'warning';

export const COLUMNS: ColumnKey[] = [
  'ring_no', 'species', 'sex', 'age', 'date', 'time', 'wing_length_mm', 'weight_g', 'additional_remark',
];

export interface Paginated<T> { items: T[]; total: number; page: number; size: number }

export interface StatusCounts { queued: number; processing: number; review: number; done: number; error: number }

export interface BatchOut {
  slug: string; name: string; created_at: string;
  num_docs: number; counts: StatusCounts; open_flags: number;
}

export interface DocOut {
  slug: string; batch_slug: string; filename: string; status: DocStatus;
  num_pages: number | null; open_flags: number; error: string | null; updated_at: string;
}

export interface PageInfo { num: number; open_flags: number; page_confidence: number | null }

export interface DocDetail extends DocOut { batch_name: string; pages: PageInfo[]; artifact_url: string }

export interface RowOut {
  idx: number; y0: number; y1: number;
  values: Record<ColumnKey, string>; corrected: ColumnKey[];
}

export interface PageRows {
  num: number; width: number; height: number; columns: ColumnKey[];
  column_x: Record<ColumnKey, [number, number]>; rows: RowOut[];
}

export interface FlagOut {
  id: number; page: number; row: number; column: ColumnKey | null;
  source: FlagSource; severity: Severity; code: string | null;
  params: Record<string, string | number>; message: string;
  suggestion: string | null; confidence: number | null; resolved: boolean;
}

export interface RowUpdate { values: Partial<Record<ColumnKey, string>> }
export interface PageUpdate { rows: Record<number, Partial<Record<ColumnKey, string>>> }
export interface ResolveOut { doc: DocOut; resolved_flag_ids: number[] }
/** Reviewer annotation, stored as a flag with source=user. column null = whole row. */
export interface FlagCreate { page: number; row: number; column: ColumnKey | null; message: string; severity?: Severity }
export interface FlagChangeOut { doc: DocOut; flags: FlagOut[] }
export interface DuplicateFiles { detail: 'duplicate_files'; filenames: string[] }

export interface ReviewItem { slug: string; batch_slug: string; filename: string; open_flags: number }
export interface ReviewQueue {
  scope: 'all' | 'batch' | 'doc'; files_left: number; flags_left: number; docs: ReviewItem[];
}

// ---- WebSocket ----
export interface Progress { step: ArtifactState; page: number; pages: number }
export interface DocEvent { type: 'doc.status'; doc: DocOut; progress: Progress | null }
export interface BatchEvent { type: 'batch.created' | 'batch.updated'; batch: BatchOut }
export interface ReviewEvent { type: 'review.pending'; files_left: number; flags_left: number }
export type ServerEvent = DocEvent | BatchEvent | ReviewEvent;
