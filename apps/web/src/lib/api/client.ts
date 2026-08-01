export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code = 'API_ERROR') {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init.headers }
    });
  } catch {
    throw new ApiError('Thikra could not reach the backend.', 0, 'NETWORK_ERROR');
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail ?? body;
    throw new ApiError(detail.message ?? `Request failed (${response.status})`, response.status, detail.code);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const money = (minor = 0, currency = 'USD') =>
  new Intl.NumberFormat('en', { style: 'currency', currency }).format(minor / 100);

export const shortDate = (value?: string | null) =>
  value ? new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : 'Not yet';

export const titleCase = (value = '') =>
  value.toLowerCase().replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
