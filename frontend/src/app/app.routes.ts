import { Routes } from '@angular/router';

import { authGuard } from './core/auth/auth';

export const routes: Routes = [
  {
    path: '',
    canActivateChild: [authGuard],
    children: [
      { path: '', loadComponent: () => import('./features/batches/batches-page').then((m) => m.BatchesPage) },
      { path: 'review', loadComponent: () => import('./features/review/review-page').then((m) => m.ReviewPage) },
      {
        path: 'review/batch/:batchSlug',
        loadComponent: () => import('./features/review/review-page').then((m) => m.ReviewPage),
      },
      {
        path: 'review/file/:docSlug',
        loadComponent: () => import('./features/review/review-page').then((m) => m.ReviewPage),
      },
      { path: ':docSlug', loadComponent: () => import('./features/document/document-page').then((m) => m.DocumentPage) },
    ],
  },
];
