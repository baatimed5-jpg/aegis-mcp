import { Routes } from '@angular/router';
import { authGuard, guestGuard } from './core/auth.guard';

export const routes: Routes = [
  {
    path: 'login',
    canActivate: [guestGuard],
    title: 'Sign in · AegisMCP',
    loadComponent: () => import('./pages/login/login').then((m) => m.LoginPage),
  },
  {
    path: '',
    canActivate: [authGuard],
    loadComponent: () => import('./layout/shell').then((m) => m.Shell),
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'overview' },
      {
        path: 'overview',
        title: 'Overview · AegisMCP',
        loadComponent: () => import('./pages/overview/overview').then((m) => m.OverviewPage),
      },
      {
        path: 'security',
        title: 'Security · AegisMCP',
        loadComponent: () => import('./pages/security/security').then((m) => m.SecurityPage),
      },
      {
        path: 'approvals',
        title: 'Approvals · AegisMCP',
        loadComponent: () => import('./pages/approvals/approvals').then((m) => m.ApprovalsPage),
      },
      {
        path: 'policies',
        title: 'Policies · AegisMCP',
        loadComponent: () => import('./pages/policies/policies').then((m) => m.PoliciesPage),
      },
      {
        path: 'experiments',
        title: 'Experiments · AegisMCP',
        loadComponent: () =>
          import('./pages/experiments/experiments').then((m) => m.ExperimentsPage),
      },
    ],
  },
  { path: '**', redirectTo: '' },
];
