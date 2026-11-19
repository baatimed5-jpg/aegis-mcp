import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { AuthService } from './auth.service';
import { isApiUrl } from './api-url';

/** Adds `Authorization: Bearer <token>` to API calls; a 401 sends the admin back to login. */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  if (!isApiUrl(req.url)) {
    return next(req);
  }
  const auth = inject(AuthService);
  const router = inject(Router);
  const token = auth.token();
  const authed =
    token && !req.headers.has('Authorization')
      ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } })
      : req;
  const isLoginCheck = req.headers.has('X-Login-Check');
  return next(isLoginCheck ? authed.clone({ headers: authed.headers.delete('X-Login-Check') }) : authed).pipe(
    catchError((err: unknown) => {
      if (!isLoginCheck && err instanceof HttpErrorResponse && err.status === 401) {
        auth.logout();
        void router.navigate(['/login'], { queryParams: { expired: 1 } });
      }
      return throwError(() => err);
    }),
  );
};
