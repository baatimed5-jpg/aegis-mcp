import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { apiUrl } from './api-url';
import {
  Approval,
  AuditRow,
  Decision,
  Experiments,
  Pipeline,
  Policies,
  SecurityEvent,
  Stats,
} from './models';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  /** Validates a candidate token before it is stored (login screen). */
  checkToken(token: string): Observable<Stats> {
    const headers = new HttpHeaders({
      Authorization: `Bearer ${token}`,
      'X-Login-Check': '1',
    });
    return this.http.get<Stats>(apiUrl('/api/stats'), { headers });
  }

  stats(): Observable<Stats> {
    return this.http.get<Stats>(apiUrl('/api/stats'));
  }

  audit(limit = 100): Observable<AuditRow[]> {
    return this.http.get<AuditRow[]>(apiUrl('/api/audit'), { params: { limit } });
  }

  securityEvents(limit = 100): Observable<SecurityEvent[]> {
    return this.http.get<SecurityEvent[]>(apiUrl('/api/security-events'), { params: { limit } });
  }

  approvals(): Observable<Approval[]> {
    return this.http.get<Approval[]>(apiUrl('/api/approvals'));
  }

  decide(id: string, decision: Decision): Observable<Approval> {
    return this.http.post<Approval>(apiUrl(`/api/approvals/${encodeURIComponent(id)}`), {
      decision,
      decided_by: 'dashboard',
    });
  }

  policies(): Observable<Policies> {
    return this.http.get<Policies>(apiUrl('/api/policies'));
  }

  pipeline(): Observable<Pipeline> {
    return this.http.get<Pipeline>(apiUrl('/api/pipeline'));
  }

  experiments(): Observable<Experiments> {
    return this.http.get<Experiments>(apiUrl('/api/experiments'));
  }

  /** Chart images need the auth header, so they are fetched as blobs. */
  chartBlob(path: string): Observable<Blob> {
    return this.http.get(apiUrl(path), { responseType: 'blob' });
  }
}
