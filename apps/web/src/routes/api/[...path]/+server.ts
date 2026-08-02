import { env } from '$env/dynamic/private';
import { isTrustedMutationOrigin } from '$lib/server/origin';
import type { RequestHandler } from './$types';

const forward: RequestHandler = async ({ request, params, url, fetch }) => {
  if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method)) {
    const origin = request.headers.get('origin');
    const trustedOrigins = [env.PUBLIC_WEB_URL, env.CSRF_TRUSTED_ORIGINS]
      .filter(Boolean)
      .join(',');
    if (!isTrustedMutationOrigin(origin, url.origin, trustedOrigins)) {
      return Response.json(
        { detail: { code: 'CSRF_ORIGIN_MISMATCH', message: 'This origin is not trusted for state-changing requests.' } },
        { status: 403 }
      );
    }
  }
  const base = env.API_INTERNAL_URL || 'http://127.0.0.1:43192';
  const target = `${base}/${params.path}${url.search}`;
  const headers = new Headers(request.headers);
  headers.delete('host');
  headers.delete('connection');
  // Local Studio uses the same scoped public commerce API as external agents.
  // The demo key remains server-only inside this same-origin BFF.
  if (params.path.startsWith('api/v1/') && !headers.has('authorization') && env.THIKRA_DEMO_API_KEY) {
    headers.set('authorization', `Bearer ${env.THIKRA_DEMO_API_KEY}`);
  }
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
