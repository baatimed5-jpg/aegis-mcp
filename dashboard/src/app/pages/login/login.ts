import { Component, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { errorText } from '../../core/util';

@Component({
  selector: 'app-login',
  imports: [FormsModule],
  template: `
    <div class="wrap">
      <form class="card login" (ngSubmit)="submit()">
        <div class="head">
          <svg viewBox="0 0 24 24" width="36" height="36" aria-hidden="true">
            <path d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3Z" fill="var(--accent)" />
            <path d="m8.5 12 2.5 2.5 4.5-5" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <h1>AegisMCP</h1>
          <p>Paste the gateway admin token to open the dashboard.</p>
        </div>
        @if (expired()) {
          <div class="alert warn">Session expired or token rejected. Sign in again.</div>
        }
        <label for="token">Admin token</label>
        <input
          id="token"
          name="token"
          type="password"
          autocomplete="off"
          spellcheck="false"
          [(ngModel)]="token"
          placeholder="ADMIN_TOKEN"
          required
        />
        @if (error()) {
          <div class="alert bad">{{ error() }}</div>
        }
        <button class="btn primary" type="submit" [disabled]="busy() || !token.trim()">
          {{ busy() ? 'Checking…' : 'Sign in' }}
        </button>
        <small>The token is kept in this browser tab only (sessionStorage).</small>
      </form>
    </div>
  `,
  styles: `
    .wrap { min-height: 100vh; display: grid; place-items: center; padding: 16px; }
    .login { width: min(400px, 100%); padding: 32px; display: flex; flex-direction: column; gap: 12px; }
    .head { text-align: center; margin-bottom: 8px; }
    .head h1 { margin: 8px 0 4px; font-size: 22px; }
    .head p { margin: 0; color: var(--muted); }
    label { font-weight: 600; font-size: 13px; }
    small { color: var(--muted); text-align: center; }
  `,
})
export class LoginPage {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  /** Bound from the `?expired=1` query param. */
  readonly expired = input<string>();

  protected token = '';
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  protected submit(): void {
    const t = this.token.trim();
    if (!t || this.busy()) return;
    this.busy.set(true);
    this.error.set(null);
    this.api.checkToken(t).subscribe({
      next: () => {
        this.auth.login(t);
        this.busy.set(false);
        void this.router.navigate(['/overview']);
      },
      error: (err: { status?: number }) => {
        this.busy.set(false);
        this.error.set(
          err?.status === 401 || err?.status === 403 ? 'Invalid admin token.' : errorText(err),
        );
      },
    });
  }
}
