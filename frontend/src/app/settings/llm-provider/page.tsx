import { LLMProviderForm } from "./form";

export const dynamic = "force-dynamic";

export default function LLMProviderSettingsPage() {
  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-12">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-medium tracking-tight">
          LLM provider
        </h1>
        <p className="text-sm opacity-70">
          Configure the model your tenant uses for chat and document
          enrichment. The platform never provides a default — you bring
          your own.
        </p>
      </header>
      <LLMProviderForm />
    </main>
  );
}
