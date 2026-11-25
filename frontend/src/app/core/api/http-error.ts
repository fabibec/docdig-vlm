import { HttpErrorResponse } from '@angular/common/http';

/** Short human-readable reason of a failed request: FastAPI's string `detail`, else the HTTP status. */
export function httpErrorMessage(err: unknown): string {
  if (!(err instanceof HttpErrorResponse)) return String(err);
  const detail = (err.error as { detail?: unknown } | null)?.detail;
  return typeof detail === 'string' ? detail : err.statusText || String(err.status);
}
