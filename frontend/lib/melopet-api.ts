const API_PREFIX = '/api';
const TOKEN_KEY = 'melopet.capability-token.v1';

type Envelope<T> = {
  requestId: string;
  status: 'ok' | 'error';
  errorCode: string | null;
  safeMessage: string | null;
  data: T | null;
};

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly httpStatus: number,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

let tokenRequest: Promise<string> | null = null;

export function getStoredToken() {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function clearStoredToken() {
  if (typeof window !== 'undefined') window.localStorage.removeItem(TOKEN_KEY);
}

export async function ensureCapabilityToken() {
  const stored = getStoredToken();
  if (stored) return stored;
  if (tokenRequest) return tokenRequest;

  tokenRequest = (async () => {
    const response = await fetch(`${API_PREFIX}/v1/capabilities/anonymous`, {
      method: 'POST',
      cache: 'no-store',
    });
    const envelope = (await response.json()) as Envelope<{ capabilityToken: string }>;
    if (!response.ok || envelope.status !== 'ok' || !envelope.data) {
      throw new ApiError(
        envelope.safeMessage ?? '无法创建本地访问身份。',
        envelope.errorCode ?? 'CAPABILITY_FAILED',
        response.status,
        envelope.requestId,
      );
    }
    window.localStorage.setItem(TOKEN_KEY, envelope.data.capabilityToken);
    return envelope.data.capabilityToken;
  })().finally(() => {
    tokenRequest = null;
  });

  return tokenRequest;
}

type ApiOptions = RequestInit & {
  authenticated?: boolean;
  idempotentWrite?: boolean;
};

export async function api<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const {
    authenticated = true,
    idempotentWrite = false,
    headers: initialHeaders,
    ...requestOptions
  } = options;
  const headers = new Headers(initialHeaders);

  if (authenticated) {
    headers.set('Authorization', `Bearer ${await ensureCapabilityToken()}`);
  }
  if (idempotentWrite) {
    headers.set('Idempotency-Key', crypto.randomUUID());
  }
  if (requestOptions.body && !(requestOptions.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  let response: Response;
  try {
    response = await fetch(`${API_PREFIX}${path}`, {
      ...requestOptions,
      headers,
      cache: 'no-store',
    });
  } catch {
    throw new ApiError('无法连接后端，请确认后端与前端服务都已启动。', 'NETWORK_ERROR', 0);
  }

  let envelope: Envelope<T>;
  try {
    envelope = (await response.json()) as Envelope<T>;
  } catch {
    throw new ApiError('后端返回了无法识别的内容。', 'INVALID_RESPONSE', response.status);
  }

  if (!response.ok || envelope.status !== 'ok' || envelope.data === null) {
    throw new ApiError(
      envelope.safeMessage ?? '请求没有完成，请稍后重试。',
      envelope.errorCode ?? 'REQUEST_FAILED',
      response.status,
      envelope.requestId,
    );
  }
  return envelope.data;
}

export async function apiBlob(path: string): Promise<Blob> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    headers: { Authorization: `Bearer ${await ensureCapabilityToken()}` },
    cache: 'no-store',
  });
  if (!response.ok) {
    throw new ApiError('桌宠预览加载失败。', 'PREVIEW_FAILED', response.status);
  }
  return response.blob();
}

export async function checkHealth() {
  return api<{ service: string; state: string }>('/healthz', { authenticated: false });
}

export async function checkReadiness() {
  return api<{
    state: string;
    ruleOnly: boolean;
    productionReady: boolean;
    productionAssets: boolean;
    issues: string[];
  }>('/readyz', { authenticated: false });
}
