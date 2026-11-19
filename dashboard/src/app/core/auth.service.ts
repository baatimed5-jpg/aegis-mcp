import { Injectable, computed, signal } from '@angular/core';

const STORAGE_KEY = 'aegis.adminToken';

function readStored(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/** Holds the admin token for this browser tab only (sessionStorage, never hard-coded). */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly _token = signal<string | null>(readStored());
  readonly token = this._token.asReadonly();
  readonly isLoggedIn = computed(() => !!this._token());

  login(token: string): void {
    const t = token.trim();
    this._token.set(t);
    try {
      sessionStorage.setItem(STORAGE_KEY, t);
    } catch {
      /* storage unavailable: keep the token in memory only */
    }
  }

  logout(): void {
    this._token.set(null);
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }
}
