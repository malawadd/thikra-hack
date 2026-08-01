import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

const forward: RequestHandler = async ({ request, params, url, fetch }) => {
  if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method)) {
    const origin = request.headers.get('origin');
    if (origin && origin !== url.origin) {
      return Response.json(
        { detail: { code: 'CSRF_ORIGIN_MISMATCH', message: 'Cross-origin state changes are not accepted.' } },
        { status: 403 }
      );
    }
  }
  const base = env.API_INTERNAL_URL || 'http://127.0.0.1:43192';
  const target = `${base}/${params.path}${url.search}`;
  const headers = new Headers(request.headers);
  headers.delete('host');
  headers.delete('connection');
  const hasBody = !['GET', 'HEAD'].includes(request.method);
  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      // @ts-expect-error Node fetch requires duplex for streamed request bodies.
      duplex: hasBody ? 'half' : undefined,
      redirect: 'manual'
    });
    const responseHeaders = new Headers();
    for (const key of ['content-type', 'cache-control', 'content-disposition', 'location', 'x-request-id', 'x-accel-buffering']) {
      const value = upstream.headers.get(key);
      if (value) responseHeaders.set(key, value);
    }
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return Response.json(
      { detail: { code: 'BACKEND_UNAVAILABLE', message: 'The FastAPI service is unavailable.' } },
      { status: 503 }
    );
  }
};

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
