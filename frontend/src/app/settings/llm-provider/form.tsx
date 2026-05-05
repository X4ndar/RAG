"use client";

import { useEffect, useMemo, useState } from "react";

import {
  ApiError,
  LLMConfigCreate,
  LLMConfigResponse,
  Provider,
  TestResult,
  llmConfigApi,
} from "@/lib/api/llm-config";

const PROVIDERS: { value: Provider; label: string; needsBaseUrl: boolean; needsKey: boolean }[] = [
  { value: "anthropic", label: "Anthropic", needsBaseUrl: false, needsKey: true },
  { value: "openai", label: "OpenAI", needsBaseUrl: false, needsKey: true },
  { value: "google", label: "Google (Gemini)", needsBaseUrl: false, needsKey: true },
  { value: "mistral", label: "Mistral", needsBaseUrl: false, needsKey: true },
  { value: "openai_compatible", label: "OpenAI-compatible (custom)", needsBaseUrl: true, needsKey: true },
  { value: "ollama", label: "Ollama", needsBaseUrl: true, needsKey: false },
];

const PROVIDER_BY_VALUE = new Map(PROVIDERS.map((p) => [p.value, p]));

type Saved = LLMConfigResponse | null;

interface FormState {
  provider: Provider;
  model_name: string;
  base_url: string;
  api_key: string;
}

const INITIAL_FORM: FormState = {
  provider: "anthropic",
  model_name: "",
  base_url: "",
  api_key: "",
};

const TENANT_KEY = "rag_tenant_id";

export function LLMProviderForm() {
  const [tenantId, setTenantId] = useState<string>(() => {
    if (typeof window === "undefined") return "";
    return window.localStorage.getItem(TENANT_KEY) ?? "";
  });
  const [tenantInputDirty, setTenantInputDirty] = useState(false);
  const [saved, setSaved] = useState<Saved>(null);
  const [form, setForm] = useState<FormState>(INITIAL_FORM);
  const [test, setTest] = useState<TestResult | null>(null);
  const [testFormSnapshot, setTestFormSnapshot] = useState<string>("");
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const [warningBanner, setWarningBanner] = useState<string | null>(null);

  // Persist tenant-id edits across reloads (dev affordance).
  useEffect(() => {
    if (typeof window === "undefined" || !tenantInputDirty) return;
    window.localStorage.setItem(TENANT_KEY, tenantId);
  }, [tenantId, tenantInputDirty]);

  const providerSpec = PROVIDER_BY_VALUE.get(form.provider)!;
  const formSnapshot = useMemo(() => JSON.stringify(form), [form]);
  const testIsForCurrentForm = test?.ok === true && testFormSnapshot === formSnapshot;
  const canTest = isUuid(tenantId) && form.model_name.trim().length > 0
    && (!providerSpec.needsBaseUrl || form.base_url.trim().length > 0)
    && (!providerSpec.needsKey || form.api_key.trim().length > 0 || (saved && saved.api_key_last4));
  const canSave = canTest && testIsForCurrentForm && !saving;

  // Load existing config when tenant id is set to a valid UUID. We do NOT
  // reset state for an invalid id — the field-level guard below renders the
  // form blank in that case without an effect.
  useEffect(() => {
    if (!isUuid(tenantId)) return;
    let alive = true;
    (async () => {
      try {
        const cfg = await llmConfigApi.get(tenantId);
        if (!alive) return;
        setSaved(cfg);
        setForm({
          provider: cfg.provider,
          model_name: cfg.model_name,
          base_url: cfg.base_url ?? "",
          api_key: "",
        });
        setWarningBanner(cfg.warning);
      } catch (err) {
        if (!alive) return;
        if (err instanceof ApiError && err.status === 404) {
          setSaved(null);
          setForm(INITIAL_FORM);
          setWarningBanner(null);
        } else {
          setErrorBanner(humanError(err));
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [tenantId]);

  function updateField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
    // Any field edit invalidates a passing test; UI must re-test before Save
    // unlocks. This is also what mitigates the typed-then-deleted edge case
    // documented in buildPayload's TODO — every keystroke nulls `test`.
    setTest(null);
  }

  async function runTest() {
    setTesting(true);
    setErrorBanner(null);
    try {
      const payload = buildPayload(form);
      const result = await llmConfigApi.test(tenantId, payload);
      setTest(result);
      setTestFormSnapshot(formSnapshot);
    } catch (err) {
      setTest({ ok: false, latency_ms: 0, error: humanError(err) });
      setTestFormSnapshot(formSnapshot);
    } finally {
      setTesting(false);
    }
  }

  async function save() {
    setSaving(true);
    setErrorBanner(null);
    try {
      const payload = buildPayload(form);
      const next = await llmConfigApi.upsert(tenantId, payload);
      setSaved(next);
      setForm({
        provider: next.provider,
        model_name: next.model_name,
        base_url: next.base_url ?? "",
        api_key: "",
      });
      setTest(null);
      setWarningBanner(next.warning);
    } catch (err) {
      setErrorBanner(humanError(err));
    } finally {
      setSaving(false);
    }
  }

  async function deleteConfig() {
    if (!confirm("Delete the saved LLM provider configuration?")) return;
    setSaving(true);
    setErrorBanner(null);
    try {
      await llmConfigApi.delete(tenantId);
      setSaved(null);
      setForm(INITIAL_FORM);
      setTest(null);
      setWarningBanner(null);
    } catch (err) {
      setErrorBanner(humanError(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <Field label="Tenant ID (dev only — V2 will replace this with auth)">
        <input
          type="text"
          value={tenantId}
          onChange={(e) => {
            setTenantInputDirty(true);
            setTenantId(e.target.value.trim());
          }}
          placeholder="00000000-0000-0000-0000-000000000000"
          className={inputClass}
          spellCheck={false}
        />
      </Field>

      <Field label="Provider">
        <select
          value={form.provider}
          onChange={(e) => updateField("provider", e.target.value as Provider)}
          className={inputClass}
        >
          {PROVIDERS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Model name">
        <input
          type="text"
          value={form.model_name}
          onChange={(e) => updateField("model_name", e.target.value)}
          placeholder={modelPlaceholder(form.provider)}
          className={inputClass}
          spellCheck={false}
        />
      </Field>

      {providerSpec.needsBaseUrl ? (
        <Field
          label="Base URL"
          hint={form.provider === "ollama"
            ? "Most Docker Desktop dev runs use http://host.docker.internal:11434/v1."
            : undefined}
        >
          <input
            type="url"
            value={form.base_url}
            onChange={(e) => updateField("base_url", e.target.value)}
            placeholder="https://api.example.com/v1"
            className={inputClass}
            spellCheck={false}
          />
        </Field>
      ) : null}

      {(providerSpec.needsKey || form.provider === "ollama") ? (
        <Field
          label={providerSpec.needsKey ? "API key" : "API key (optional)"}
          hint={form.provider === "ollama"
            ? "Most Ollama installs don't require a key."
            : "Leave blank to keep the saved key."}
        >
          <div className="flex items-center gap-2">
            <input
              type="password"
              value={form.api_key}
              onChange={(e) => updateField("api_key", e.target.value)}
              placeholder={saved?.api_key_last4 ? "" : "sk-…"}
              className={inputClass}
              autoComplete="off"
            />
            {saved?.api_key_last4 ? (
              <span
                className="rounded-md border px-2 py-1 text-xs font-mono opacity-80"
                title="Last 4 chars of the saved key"
              >
                ●●●●●●●●{saved.api_key_last4}
              </span>
            ) : null}
          </div>
        </Field>
      ) : null}

      <TestSection
        test={test}
        testing={testing}
        canTest={!!canTest}
        runTest={runTest}
        savedTestStatus={saved?.test_status ?? null}
        savedTestedAt={saved?.tested_at ?? null}
      />

      {errorBanner ? <Banner kind="error">{errorBanner}</Banner> : null}
      {warningBanner ? <Banner kind="warn">{warningBanner}</Banner> : null}

      <div className="flex items-center justify-between gap-3 pt-2">
        <button
          type="button"
          onClick={save}
          disabled={!canSave}
          className={primaryBtnClass}
          title={!testIsForCurrentForm
            ? "Run a successful test for the current values before saving."
            : undefined}
        >
          {saving ? "Saving…" : saved ? "Save changes" : "Save"}
        </button>
        {saved ? (
          <button
            type="button"
            onClick={deleteConfig}
            disabled={saving}
            className={dangerBtnClass}
          >
            Delete saved configuration
          </button>
        ) : null}
      </div>
    </div>
  );
}

// ---------- helpers --------------------------------------------------------

// TODO (awareness, V1.5b): "exact field set" comparison currently treats
// api_key="" (keep saved) and api_key="abc"-then-deleted as the same
// submitted value, but a test result that ran against "abc" is not valid
// for the saved key. V1.5a workaround: `keyTypedThisSession` (set in
// updateField) flips `test` to null on any keystroke, so the user must
// re-test before Save unlocks. A fuller session log lands in V1.5b.
function buildPayload(form: FormState): LLMConfigCreate {
  const apiKey = form.api_key.trim();
  return {
    provider: form.provider,
    model_name: form.model_name.trim(),
    base_url: form.base_url.trim() || null,
    // Empty input = keep the saved key on update; server enforces
    // create-vs-update branch and rejects empty key on a create for
    // non-ollama providers with a 400.
    api_key: apiKey ? apiKey : null,
  };
}

function modelPlaceholder(provider: Provider): string {
  return {
    anthropic: "claude-sonnet-4-5",
    openai: "gpt-5",
    google: "gemini-2.0-flash",
    mistral: "mistral-large-latest",
    openai_compatible: "your-model-id",
    ollama: "qwen3",
  }[provider];
}

function isUuid(s: string): boolean {
  return /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(s);
}

function humanError(err: unknown): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return String(err);
}

// ---------- presentational primitives -------------------------------------

const inputClass =
  "w-full rounded-md border bg-transparent px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-offset-1";
const primaryBtnClass =
  "rounded-md border px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50";
const dangerBtnClass =
  "rounded-md border border-red-300 px-3 py-2 text-sm text-red-700 disabled:opacity-50";

function Field(props: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-sm font-medium">{props.label}</span>
      {props.children}
      {props.hint ? <span className="text-xs opacity-70">{props.hint}</span> : null}
    </label>
  );
}

function Banner(props: { kind: "warn" | "error"; children: React.ReactNode }) {
  const cls =
    props.kind === "warn"
      ? "border-yellow-400 bg-yellow-50 text-yellow-900"
      : "border-red-400 bg-red-50 text-red-900";
  return (
    <div className={`rounded-md border px-3 py-2 text-sm ${cls}`}>
      {props.children}
    </div>
  );
}

function TestSection(props: {
  test: TestResult | null;
  testing: boolean;
  canTest: boolean;
  runTest: () => void;
  savedTestStatus: string | null;
  savedTestedAt: string | null;
}) {
  const { test, testing, canTest, runTest, savedTestStatus, savedTestedAt } = props;
  return (
    <div className="flex flex-col gap-2 rounded-md border p-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={runTest}
          disabled={!canTest || testing}
          className={primaryBtnClass}
        >
          {testing ? "Testing…" : "Test connection"}
        </button>
        <span className="text-xs opacity-70">
          {savedTestStatus && savedTestedAt
            ? `Last saved test: ${savedTestStatus} on ${new Date(savedTestedAt).toLocaleString()}`
            : "No test on file."}
        </span>
      </div>
      {test ? (
        test.ok ? (
          <p className="text-green-700">
            Test passed in {(test.latency_ms / 1000).toFixed(1)} s. Save is now enabled.
          </p>
        ) : (
          <p className="text-red-700">
            Test failed: {test.error ?? "no detail"}
          </p>
        )
      ) : null}
    </div>
  );
}
