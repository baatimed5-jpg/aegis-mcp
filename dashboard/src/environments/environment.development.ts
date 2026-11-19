/**
 * Dev server (`npm start` on :4200) talks directly to the gateway on :8000.
 * The backend enables CORS for http://localhost:4200.
 */
export const environment = {
  production: false,
  apiBase: 'http://localhost:8000',
};
