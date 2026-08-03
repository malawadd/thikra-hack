const API = 'http://127.0.0.1:43192';

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set('content-type', 'application/json');
  const response = await fetch(`${API}${path}`, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail ?? body;
    throw new ApiError(response.status, detail.code ?? 'REQUEST_FAILED', detail.message ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function studioEvents(executionId: string, onEvent: (event: MessageEvent<string>) => void, onError: () => void): EventSource {
  const source = new EventSource(`${API}/studio/executions/${executionId}/events`);
  source.onmessage = onEvent;
  source.onerror = onError;
  return source;
}

export function renderEvents(renderId: string, onEvent: (event: MessageEvent<string>) => void, onError: () => void): EventSource {
  const source = new EventSource(`${API}/studio/renders/${renderId}/events`);
  source.onmessage = onEvent;
  source.onerror = onError;
  return source;
}

export const assetUrl = (id: string) => `${API}/studio/assets/${id}/content`;
export const assetThumbnailUrl = (id: string) => `${API}/studio/assets/${id}/thumbnail`;
export const assetProxyUrl = (id: string) => `${API}/studio/assets/${id}/proxy`;
export const assetDownloadUrl = (id: string) => `${API}/studio/assets/${id}/download`;
