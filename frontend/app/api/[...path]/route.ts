import type { NextRequest } from 'next/server';

type RouteContext = { params: Promise<{ path: string[] }> };

const forwardedRequestHeaders = [
  'authorization',
  'content-type',
  'idempotency-key',
  'x-request-id',
];

async function proxy(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  const origin = (process.env.MELOPET_API_ORIGIN ?? 'http://127.0.0.1:8010').replace(/\/$/, '');
  const upstreamUrl = `${origin}/${path.join('/')}${request.nextUrl.search}`;
  const headers = new Headers();

  for (const name of forwardedRequestHeaders) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const hasBody = !['GET', 'HEAD'].includes(request.method);
  const response = await fetch(upstreamUrl, {
    method: request.method,
    headers,
    body: hasBody ? await request.arrayBuffer() : undefined,
    cache: 'no-store',
    redirect: 'manual',
  });

  const responseHeaders = new Headers();
  for (const name of ['content-type', 'x-request-id', 'cache-control']) {
    const value = response.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }

  return new Response(response.body, {
    status: response.status,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const DELETE = proxy;
