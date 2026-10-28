import { HttpInterceptorFn } from '@angular/common/http';
import { CanActivateFn } from '@angular/router';

/*
 * Placeholders for the later Nextcloud login (see docs/backend.md "Later: auth + Nextcloud").
 * The backend will set an HttpOnly session cookie, so the interceptor will mostly redirect to
 * /login on 401; the guard will check a session endpoint. Both are no-ops for now.
 */

export const authInterceptor: HttpInterceptorFn = (req, next) => next(req);

export const authGuard: CanActivateFn = () => true;
