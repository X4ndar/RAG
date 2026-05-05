/**
 * Typed fetch wrappers for /api/v1/admin/llm-config.
 *
 * Mirrors the Pydantic schemas in backend/app/models/llm_config.py.
 * V1.5a sends `X-Tenant-ID` as the only auth signal — the real auth
 * layer arrives in V2.
 */

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type Provider =
  | "anthropic"
  | "openai"
  | "google"
  | "mistral"
  | "openai_compatible"
  | "ollama";

export type TestStatus = "untested" | "passed" | "failed";

export interface LLMConfigCreate {
  provider: Provider;
  model_name: string;
  base_url?: string | null;
  api_key?: string | null;
}

export interface LLMConfigResponse {
  id: string;
  provider: Provider;
  model_name: string;
  base_url: string | null;
  api_key_last4: string;
  test_status: TestStatus;
  test_error: string | null;
  tested_at: string | null;
  created_at: string;
  updated_at: string;
  warning: string | null;
}

export interface TestResult {
  ok: boolean;
  latency_ms: number;
  error: string | null;
}

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

async function call<T>(
  path: string,
  opts: { method: string; tenantId: string; body?: unknown },
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: opts.method,
    headers: {
      "X-Tenant-ID": opts.tenantId,
      "Content-Type": "application/json",
    },
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    cache: "no-store",
  });
  if (res.status === 204) {
    return null as T;
  }
  const text = await res.text();
  let parsed: unknown = undefined;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      /* keep parsed=undefined */
    }
  }
  if (!res.ok) {
    const detail =
      parsed && typeof parsed === "object" && "detail" in parsed
        ? String((parsed as { detail: unknown }).detail)
        : text || res.statusText;
    throw new ApiError(res.status, detail);
  }
  return parsed as T;
}

export const llmConfigApi = {
  get: (tenantId: string): Promise<LLMConfigResponse> =>
    call<LLMConfigResponse>("/api/v1/admin/llm-config", {
      method: "GET",
      tenantId,
    }),

  upsert: (
    tenantId: string,
    body: LLMConfigCreate,
  ): Promise<LLMConfigResponse> =>
    call<LLMConfigResponse>("/api/v1/admin/llm-config", {
      method: "POST",
      tenantId,
      body,
    }),

  delete: (tenantId: string): Promise<null> =>
    call<null>("/api/v1/admin/llm-config", {
      method: "DELETE",
      tenantId,
    }),

  test: (tenantId: string, body: LLMConfigCreate): Promise<TestResult> =>
    call<TestResult>("/api/v1/admin/llm-config/test", {
      method: "POST",
      tenantId,
      body,
    }),
};
