import { Component, OnDestroy, OnInit, computed, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from '../core/auth.service';
import { AuditStreamService } from '../core/audit-stream.service';

interface NavItem {
  path: string;
  label: string;
  icon: string;
}

@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <div class="shell">
      <aside class="sidebar">
        <div class="brand">
          <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
            <path d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3Z" fill="var(--accent)" />
            <path d="m8.5 12 2.5 2.5 4.5-5" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <div>
            <strong>AegisMCP</strong>
            <span>Gateway admin</span>
          </div>
        </div>
        <nav>
          @for (item of nav; track item.path) {
            <a [routerLink]="item.path" routerLinkActive="active">
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path [attr.d]="item.icon" /></svg>
              {{ item.label }}
            </a>
          }
        </nav>
        <div class="sidebar-foot">
          <span class="live" [class]="'live ' + stream.state()">
            <i></i>{{ streamLabel() }}
          </span>
          <button class="btn ghost small" type="button" (click)="logout()">Sign out</button>
        </div>
      </aside>
      <main class="content">
        <router-outlet />
      </main>
    </div>
  `,
  styles: `
    .shell { display: grid; grid-template-columns: 232px 1fr; min-height: 100vh; }
    .sidebar {
      background: var(--panel); border-right: 1px solid var(--border);
      display: flex; flex-direction: column; padding: 20px 14px; position: sticky; top: 0; height: 100vh;
    }
    .brand { display: flex; gap: 10px; align-items: center; padding: 0 8px 22px; }
    .brand strong { display: block; font-size: 16px; letter-spacing: .2px; }
    .brand span { display: block; font-size: 12px; color: var(--muted); }
    nav { display: flex; flex-direction: column; gap: 2px; flex: 1; }
    nav a {
      display: flex; align-items: center; gap: 10px; padding: 9px 10px; border-radius: 8px;
      color: var(--text-2); text-decoration: none; font-weight: 500;
    }
    nav a svg { fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    nav a:hover { background: var(--hover); color: var(--text); }
    nav a.active { background: var(--accent-soft); color: var(--accent); }
    .sidebar-foot { display: flex; flex-direction: column; gap: 10px; padding: 0 8px; }
    .live { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); }
    .live i { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
    .live.open i { background: var(--ok); box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 25%, transparent); }
    .live.reconnecting i, .live.connecting i { background: var(--warn); }
    .content { padding: 28px 32px 48px; min-width: 0; }
    @media (max-width: 860px) {
      .shell { grid-template-columns: 1fr; }
      .sidebar { position: static; height: auto; flex-direction: row; flex-wrap: wrap; align-items: center; gap: 8px; padding: 12px 16px; }
      .brand { padding: 0; }
      nav { flex-direction: row; flex-wrap: wrap; flex-basis: 100%; order: 3; }
      .sidebar-foot { flex-direction: row; margin-left: auto; align-items: center; padding: 0; }
      .content { padding: 20px 16px 40px; }
    }
  `,
})
export class Shell implements OnInit, OnDestroy {
  protected readonly stream = inject(AuditStreamService);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly nav: NavItem[] = [
    { path: 'overview', label: 'Overview', icon: 'M4 13h6V4H4zM14 20h6v-9h-6zM4 20h6v-3H4zM14 4v3h6V4z' },
    { path: 'security', label: 'Security', icon: 'M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6zM12 9v4M12 16h.01' },
    { path: 'approvals', label: 'Approvals', icon: 'M9 11l3 3 8-8M20 12v7a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h11' },
    { path: 'policies', label: 'Policies', icon: 'M6 3h9l4 4v14H6zM9 12h7M9 16h7M9 8h3' },
    { path: 'experiments', label: 'Experiments', icon: 'M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-9V3' },
  ];

  protected readonly streamLabel = computed(() => {
    switch (this.stream.state()) {
      case 'open':
        return 'Live feed connected';
      case 'connecting':
        return 'Connecting…';
      case 'reconnecting':
        return 'Reconnecting…';
      default:
        return 'Live feed idle';
    }
  });

  ngOnInit(): void {
    this.stream.start();
  }

  ngOnDestroy(): void {
    this.stream.stop();
  }

  protected logout(): void {
    this.stream.stop();
    this.auth.logout();
    void this.router.navigate(['/login']);
  }
}
