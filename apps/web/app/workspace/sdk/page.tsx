"use client";

import React, { useState } from "react";
import { apiBase } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n/context";

function strTuples(v: unknown): Array<[string, string]> {
  if (!Array.isArray(v)) return [];
  const out: Array<[string, string]> = [];
  for (const row of v) {
    if (
      Array.isArray(row) &&
      row.length >= 2 &&
      typeof row[0] === "string" &&
      typeof row[1] === "string"
    ) {
      out.push([row[0], row[1]]);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Endpoint constants
// ---------------------------------------------------------------------------
//
// The collector exposes the OTLP/HTTP traces endpoint at TWO paths:
//
//   * `/otlp/v1/traces` — the historical EasyObs path used by the bundled
//     `easyobs_agent` Python SDK.
//   * `/v1/traces`      — the spec-mandated alias so any stock OpenTelemetry
//     SDK works with just `OTEL_EXPORTER_OTLP_ENDPOINT=<base-url>` and no
//     per-signal URL override.
//
// We surface both in the UI so each tab can show whatever path is canonical
// for its ecosystem.
const PATH_EASYOBS = "/otlp/v1/traces";
const PATH_OTEL_STD = "/v1/traces";

const TOKEN_PLACEHOLDER = "<your-ingest-token>";
const SERVICE_PLACEHOLDER = "my-agent";

// ===========================================================================
// PYTHON guide content
// ===========================================================================

function pyInitSnippet(token: string, baseUrl: string, service: string): string {
  return `from easyobs_agent import init, traced

init("${baseUrl}",
     token="${token}",
     service="${service}",
     auto=True)          # = capture_io=True + auto_langchain=True

@traced("agent.turn")
def answer(user_query: str) -> str:
    return llm.invoke(user_query).content`;
}

const PY_TRACED_SNIPPET = `from easyobs_agent import traced

@traced
def step_a(q: str) -> str:           # span name = "step_a"
    return ...

@traced("rag.retrieve")
def step_b(q: str) -> list[str]:     # fixed span name
    return ...

@traced("safe.path", capture=False)
def step_c(secret: str) -> None:     # opt-out of args/return capture
    ...

@traced("force.io", capture=True)
def step_d(q: str) -> str:           # force-capture even if init() didn't
    return ...

@traced("async.turn")                # works on coroutines too
async def step_e(q: str) -> str:
    return await llm.ainvoke(q)`;

const PY_SPAN_BLOCK_SNIPPET = `from easyobs_agent import span_block, record_llm, record_retrieval, record_tool

with span_block("retrieve", kind="retrieve", step="vector.lookup"):
    docs = vector.search(q)
    record_retrieval(query=q, docs=docs)

with span_block("generate", kind="llm"):
    reply = my_local_llm(prompt)
    record_llm(
        model="local-llm-7b",
        query=prompt,
        response=reply,
        tokens_in=128,
        tokens_out=256,
    )

with span_block("postprocess", kind="tool"):
    out = format_markdown(reply)
    record_tool(name="format.markdown", inp=reply, out=out)`;

const PY_SPAN_TAG_SNIPPET = `from easyobs_agent import traced, span_tag, span_block, SpanTag

@traced("agent.turn")
def answer(q: str, session_id: str) -> str:
    span_tag(SpanTag.SESSION, session_id)   # → tagged on agent.turn span
    span_tag(SpanTag.USER, "alice")
    span_tag(SpanTag.QUERY, q)

    with span_block("retrieve", kind="retrieve"):
        span_tag(SpanTag.TARGET, "faq-index")  # → tagged on retrieve span
        docs = vector.search(q)

    reply = compose(docs)

    span_tag(SpanTag.MODEL, "gpt-4o-mini")
    span_tag(SpanTag.TOKENS_IN, 128)
    span_tag(SpanTag.TOKENS_OUT, 256)
    span_tag(SpanTag.RESPONSE, reply)
    return reply`;

// ===========================================================================
// TS / JS guide content
//
// Strategy: rather than ship a private `@easyobs/sdk-node` package on day 1,
// we standardise on the official OpenTelemetry JS SDK and document the
// EasyObs-specific bits (URL, bearer header, `o.*` attribute namespace).
// This is the same pattern Langfuse and Phoenix use for "any other OTel
// language" and gives us first-class TS/JS coverage without an extra build
// pipeline. A thin convenience wrapper can land later.
// ===========================================================================

const TS_INSTALL_SNIPPET = `npm install \\
  @opentelemetry/api \\
  @opentelemetry/sdk-node \\
  @opentelemetry/exporter-trace-otlp-proto \\
  @opentelemetry/resources \\
  @opentelemetry/semantic-conventions`;

function tsInitSnippet(token: string, baseUrl: string, service: string): string {
  return `// tracing.ts — import this *first* in your entry file.
import { NodeSDK } from "@opentelemetry/sdk-node";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";
import { Resource } from "@opentelemetry/resources";
import { SemanticResourceAttributes } from "@opentelemetry/semantic-conventions";

export const sdk = new NodeSDK({
  resource: new Resource({
    [SemanticResourceAttributes.SERVICE_NAME]: "${service}",
  }),
  traceExporter: new OTLPTraceExporter({
    url: "${baseUrl}${PATH_OTEL_STD}",
    headers: {
      Authorization: "Bearer ${token}",
    },
  }),
});

sdk.start();

// flush the last batch on graceful shutdown
process.on("SIGTERM", () => sdk.shutdown().catch(() => {}));`;
}

const TS_TRACED_SNIPPET = `import { trace, SpanStatusCode } from "@opentelemetry/api";

const tracer = trace.getTracer("my-agent");

// Reusable wrapper — the JS equivalent of Python's @traced("name").
export function traced<T>(name: string, fn: () => Promise<T>): Promise<T> {
  return tracer.startActiveSpan(name, async (span) => {
    try {
      const out = await fn();
      span.setStatus({ code: SpanStatusCode.OK });
      return out;
    } catch (err) {
      span.recordException(err as Error);
      span.setStatus({ code: SpanStatusCode.ERROR });
      throw err;
    } finally {
      span.end();
    }
  });
}

export async function answer(q: string) {
  return traced("agent.turn", async () => {
    const span = trace.getActiveSpan()!;
    span.setAttribute("o.q", q);          // input prompt
    span.setAttribute("o.user", "alice");
    const reply = await llm.invoke(q);
    span.setAttribute("o.r", reply);      // final response
    return reply;
  });
}`;

const TS_SPAN_BLOCK_SNIPPET = `import { trace } from "@opentelemetry/api";

const tracer = trace.getTracer("my-agent");

export async function ragTurn(q: string) {
  return tracer.startActiveSpan("agent.turn", async (root) => {
    root.setAttribute("o.kind", "agent");
    root.setAttribute("o.q", q);

    // ---- retrieve span ----
    const docs = await tracer.startActiveSpan("retrieve", async (sp) => {
      sp.setAttribute("o.kind", "retrieve");
      sp.setAttribute("o.target", "faq-index");
      try {
        return await vector.search(q);
      } finally {
        sp.end();
      }
    });

    // ---- llm span ----
    const reply = await tracer.startActiveSpan("generate", async (sp) => {
      sp.setAttribute("o.kind", "llm");
      sp.setAttribute("o.model", "gpt-4o-mini");
      try {
        const out = await llm.invoke({ q, docs });
        sp.setAttribute("o.tok.in", out.usage.input);
        sp.setAttribute("o.tok.out", out.usage.output);
        return out.text;
      } finally {
        sp.end();
      }
    });

    root.setAttribute("o.r", reply);
    root.end();
    return reply;
  });
}`;

const TS_SPAN_TAG_SNIPPET = `import { trace } from "@opentelemetry/api";

export function tagActive(attrs: Record<string, string | number | boolean>) {
  const span = trace.getActiveSpan();
  if (!span) return;
  for (const [k, v] of Object.entries(attrs)) {
    span.setAttribute(k, v);   // writes onto the CURRENTLY-active span
  }
}

// usage inside a traced("agent.turn", …) block:
tagActive({
  "o.sess": sessionId,
  "o.user": "alice",
  "o.q": userQuery,
});

// later, after the LLM call:
tagActive({
  "o.model": "gpt-4o-mini",
  "o.tok.in": 128,
  "o.tok.out": 256,
  "o.r": reply,
});`;

// ===========================================================================
// Raw OTLP/HTTP guide content
// ===========================================================================

function curlSnippet(token: string, baseUrl: string): string {
  return `curl -X POST ${baseUrl}${PATH_OTEL_STD} \\
  -H "Authorization: Bearer ${token}" \\
  -H "Content-Type: application/json" \\
  --data @sample_ingest.json`;
}

const RAW_JSON_SAMPLE = `{
  "resourceSpans": [{
    "resource": {
      "attributes": [
        {"key": "service.name", "value": {"stringValue": "my-agent"}}
      ]
    },
    "scopeSpans": [{
      "spans": [{
        "traceId":  "5b8aa5a2d2c872e8321cf37308d69df2",
        "spanId":   "051581bf3cb55c13",
        "name":     "agent.turn",
        "kind":     1,
        "startTimeUnixNano": "1730000000000000000",
        "endTimeUnixNano":   "1730000000750000000",
        "status":   {"code": 1},
        "attributes": [
          {"key": "o.kind",    "value": {"stringValue": "agent"}},
          {"key": "o.q",       "value": {"stringValue": "summarise yesterday"}},
          {"key": "o.r",       "value": {"stringValue": "..."}},
          {"key": "o.model",   "value": {"stringValue": "gpt-4o-mini"}},
          {"key": "o.tok.in",  "value": {"intValue": 128}},
          {"key": "o.tok.out", "value": {"intValue": 256}}
        ]
      }]
    }]
  }]
}`;

// ===========================================================================
// Tokenizers — Python and TS share enough that we use the same colour
// classes (.t-com / .t-str / .t-kw / .t-bool / .t-deco / .t-fn / .t-arg)
// from globals.css. The shell tokenizer just colours comments and quoted
// strings; the rest stays default.
// ===========================================================================

type TokKind = "txt" | "com" | "str" | "kw" | "bool" | "deco" | "fn" | "arg";
interface Tok {
  kind: TokKind;
  text: string;
}

// Python: keywords, decorators, strings, kwargs (`name=`), known SDK fns.
const PY_RE =
  /(#[^\n]*)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(@\w+(?:\.\w+)*)|\b(from|import|def|with|return|as|class|in|for|if|else|elif|raise|try|except|finally|yield|lambda|while|await|async)\b|\b(True|False|None)\b|\b(init|traced|span_block|span_tag|record_llm|record_retrieval|record_tool|record_session|SpanTag|EasyObsCallbackHandler)\b|\b(\w+)(?=\s*=(?!=))/g;

function tokenizePython(src: string): Tok[] {
  const out: Tok[] = [];
  let cursor = 0;
  PY_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = PY_RE.exec(src)) !== null) {
    if (m.index > cursor) out.push({ kind: "txt", text: src.slice(cursor, m.index) });
    if (m[1]) out.push({ kind: "com", text: m[1] });
    else if (m[2]) out.push({ kind: "str", text: m[2] });
    else if (m[3]) out.push({ kind: "deco", text: m[3] });
    else if (m[4]) out.push({ kind: "kw", text: m[4] });
    else if (m[5]) out.push({ kind: "bool", text: m[5] });
    else if (m[6]) out.push({ kind: "fn", text: m[6] });
    else if (m[7]) out.push({ kind: "arg", text: m[7] });
    cursor = m.index + m[0].length;
  }
  if (cursor < src.length) out.push({ kind: "txt", text: src.slice(cursor) });
  return out;
}

// TS / JS: line + block comments, single/double/backtick strings, JS keywords,
// known OTel surface names, and object-literal keys (`name:`) so the EasyObs
// `o.*` attribute names jump out.
const TS_RE =
  /(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|\b(import|from|export|const|let|var|function|return|async|await|class|extends|new|if|else|for|while|try|catch|finally|throw|of|in|as|interface|type|enum|default|public|private|protected|static)\b|\b(true|false|null|undefined)\b|\b(NodeSDK|OTLPTraceExporter|Resource|SemanticResourceAttributes|trace|tracer|SpanStatusCode|startActiveSpan|setAttribute|setAttributes|recordException|getActiveSpan|getTracer|setStatus|end|shutdown|start)\b|\b([A-Za-z_$][\w$]*)(?=\s*:)/g;

function tokenizeTs(src: string): Tok[] {
  const out: Tok[] = [];
  let cursor = 0;
  TS_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TS_RE.exec(src)) !== null) {
    if (m.index > cursor) out.push({ kind: "txt", text: src.slice(cursor, m.index) });
    if (m[1]) out.push({ kind: "com", text: m[1] });
    else if (m[2]) out.push({ kind: "str", text: m[2] });
    else if (m[3]) out.push({ kind: "kw", text: m[3] });
    else if (m[4]) out.push({ kind: "bool", text: m[4] });
    else if (m[5]) out.push({ kind: "fn", text: m[5] });
    else if (m[6]) out.push({ kind: "arg", text: m[6] });
    cursor = m.index + m[0].length;
  }
  if (cursor < src.length) out.push({ kind: "txt", text: src.slice(cursor) });
  return out;
}

// Shell: comments + quoted args. Keep simple — the cURL example doesn't
// need full bash highlighting.
const SH_RE = /(#[^\n]*)|("(?:\\.|[^"\\])*")/g;
function tokenizeSh(src: string): Tok[] {
  const out: Tok[] = [];
  let cursor = 0;
  SH_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = SH_RE.exec(src)) !== null) {
    if (m.index > cursor) out.push({ kind: "txt", text: src.slice(cursor, m.index) });
    if (m[1]) out.push({ kind: "com", text: m[1] });
    else if (m[2]) out.push({ kind: "str", text: m[2] });
    cursor = m.index + m[0].length;
  }
  if (cursor < src.length) out.push({ kind: "txt", text: src.slice(cursor) });
  return out;
}

// JSON: comments don't apply, just colour string literals. Numbers, booleans,
// nulls re-use the existing palette.
const JSON_RE = /("(?:\\.|[^"\\])*")|\b(true|false|null)\b|\b(\d+(?:\.\d+)?)\b/g;
function tokenizeJson(src: string): Tok[] {
  const out: Tok[] = [];
  let cursor = 0;
  JSON_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = JSON_RE.exec(src)) !== null) {
    if (m.index > cursor) out.push({ kind: "txt", text: src.slice(cursor, m.index) });
    if (m[1]) out.push({ kind: "str", text: m[1] });
    else if (m[2]) out.push({ kind: "bool", text: m[2] });
    else if (m[3]) out.push({ kind: "bool", text: m[3] });
    cursor = m.index + m[0].length;
  }
  if (cursor < src.length) out.push({ kind: "txt", text: src.slice(cursor) });
  return out;
}

type Lang = "py" | "ts" | "sh" | "json";

function CodeBlock({ src, lang }: { src: string; lang: Lang }) {
  const toks =
    lang === "py"
      ? tokenizePython(src)
      : lang === "ts"
        ? tokenizeTs(src)
        : lang === "json"
          ? tokenizeJson(src)
          : tokenizeSh(src);
  return (
    <pre className="eo-code">
      {toks.map((t, i) =>
        t.kind === "txt" ? (
          <React.Fragment key={i}>{t.text}</React.Fragment>
        ) : (
          <span key={i} className={`t-${t.kind}`}>
            {t.text}
          </span>
        )
      )}
    </pre>
  );
}

// ---------------------------------------------------------------------------
// Reusable atoms
// ---------------------------------------------------------------------------

function CopyBtn({ text, label }: { text: string; label?: string }) {
  const { t } = useI18n();
  const [done, setDone] = useState(false);
  const copyLabel = label ?? t("sdk.copy");
  return (
    <button
      type="button"
      className="eo-btn eo-btn-ghost"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {}
      }}
    >
      {done ? t("sdk.copied") : copyLabel}
    </button>
  );
}

/**
 * One self-contained guide card — title + options table + snippet + hint.
 * Used identically across the Python, TS/JS and OTLP/HTTP tabs so each
 * surface has the same visual rhythm: "what knobs are available, then a
 * runnable example using exactly those knobs".
 */
function GuideCard({
  title,
  sub,
  options,
  snippet,
  hint,
  lang,
}: {
  title: string;
  sub: string;
  options: Array<[string, string]>;
  snippet: string;
  hint?: React.ReactNode;
  lang: Lang;
}) {
  const { t } = useI18n();
  return (
    <section className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">{title}</h3>
        <span className="eo-card-sub">{sub}</span>
        <div className="eo-card-actions">
          <CopyBtn text={snippet} label={t("sdk.copySnippet")} />
        </div>
      </div>
      <dl className="eo-kv-list eo-kv-list-code">
        {options.map(([k, v]) => (
          <div className="eo-kv-row" key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <div className="eo-divider" />
      <CodeBlock src={snippet} lang={lang} />
      {hint && <p className="eo-hint">{hint}</p>}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Per-tab panels
// ---------------------------------------------------------------------------

function PythonPanel({ baseUrl, orgName }: { baseUrl: string; orgName: string }) {
  const { t, raw, tsub } = useI18n();
  return (
    <>
      <section className="eo-card">
        <div className="eo-card-h">
          <h3 className="eo-card-title">{t("sdk.python.endpointTitle")}</h3>
          <span className="eo-card-sub">{t("sdk.python.endpointSub")}</span>
        </div>
        <dl className="eo-kv-list">
          <div className="eo-kv-row">
            <dt>{t("sdk.python.url")}</dt>
            <dd>
              <code>
                {baseUrl}
                {PATH_EASYOBS}
              </code>
              <CopyBtn text={`${baseUrl}${PATH_EASYOBS}`} />
            </dd>
          </div>
          <div className="eo-kv-row">
            <dt>{t("sdk.python.auth")}</dt>
            <dd>
              <code>Authorization: Bearer &lt;token&gt;</code>
            </dd>
          </div>
          <div className="eo-kv-row">
            <dt>{t("sdk.python.install")}</dt>
            <dd>
              <code>pip install -e &apos;.[agent]&apos;</code>
            </dd>
          </div>
        </dl>
        <p className="eo-hint">{tsub("sdk.python.tokenHint", { org: orgName })}</p>
      </section>

      <GuideCard
        title={t("sdk.python.initTitle")}
        sub={t("sdk.python.initSub")}
        options={strTuples(raw("sdk.python.initOptions"))}
        snippet={pyInitSnippet(TOKEN_PLACEHOLDER, baseUrl, SERVICE_PLACEHOLDER)}
        lang="py"
        hint={t("sdk.python.initHint")}
      />

      <GuideCard
        title={t("sdk.python.tracedTitle")}
        sub={t("sdk.python.tracedSub")}
        options={strTuples(raw("sdk.python.tracedOptions"))}
        snippet={PY_TRACED_SNIPPET}
        lang="py"
        hint={t("sdk.python.tracedHint")}
      />

      <GuideCard
        title={t("sdk.python.spanBlockTitle")}
        sub={t("sdk.python.spanBlockSub")}
        options={strTuples(raw("sdk.python.spanBlockOptions"))}
        snippet={PY_SPAN_BLOCK_SNIPPET}
        lang="py"
        hint={t("sdk.python.spanBlockHint")}
      />

      <GuideCard
        title={t("sdk.python.spanTagTitle")}
        sub={t("sdk.python.spanTagSub")}
        options={strTuples(raw("sdk.python.spanTagOptions"))}
        snippet={PY_SPAN_TAG_SNIPPET}
        lang="py"
        hint={t("sdk.python.spanTagHint")}
      />
    </>
  );
}

function TsJsPanel({ baseUrl, orgName }: { baseUrl: string; orgName: string }) {
  const { t, raw, tsub } = useI18n();
  return (
    <>
      <section className="eo-card">
        <div className="eo-card-h">
          <h3 className="eo-card-title">{t("sdk.ts.endpointTitle")}</h3>
          <span className="eo-card-sub">{t("sdk.ts.endpointSub")}</span>
        </div>
        <dl className="eo-kv-list">
          <div className="eo-kv-row">
            <dt>{t("sdk.ts.url")}</dt>
            <dd>
              <code>
                {baseUrl}
                {PATH_OTEL_STD}
              </code>
              <CopyBtn text={`${baseUrl}${PATH_OTEL_STD}`} />
            </dd>
          </div>
          <div className="eo-kv-row">
            <dt>{t("sdk.ts.auth")}</dt>
            <dd>
              <code>Authorization: Bearer &lt;token&gt;</code>
            </dd>
          </div>
          <div className="eo-kv-row">
            <dt>{t("sdk.ts.wire")}</dt>
            <dd>
              <code>application/x-protobuf</code> (recommended) ·{" "}
              <code>application/json</code>
            </dd>
          </div>
          <div className="eo-kv-row">
            <dt>{t("sdk.ts.importRow")}</dt>
            <dd>{t("sdk.ts.importRowVal")}</dd>
          </div>
        </dl>
        <p className="eo-hint">{tsub("sdk.ts.tokenHint", { org: orgName })}</p>
      </section>

      <GuideCard
        title={t("sdk.ts.installTitle")}
        sub={t("sdk.ts.installSub")}
        options={strTuples(raw("sdk.ts.installOptions"))}
        snippet={TS_INSTALL_SNIPPET}
        lang="sh"
        hint={t("sdk.ts.installHint")}
      />

      <GuideCard
        title={t("sdk.ts.initTitle")}
        sub={t("sdk.ts.initSub")}
        options={strTuples(raw("sdk.ts.initOptions"))}
        snippet={tsInitSnippet(TOKEN_PLACEHOLDER, baseUrl, SERVICE_PLACEHOLDER)}
        lang="ts"
        hint={t("sdk.ts.initHint")}
      />

      <GuideCard
        title={t("sdk.ts.tracedTitle")}
        sub={t("sdk.ts.tracedSub")}
        options={strTuples(raw("sdk.ts.tracedOptions"))}
        snippet={TS_TRACED_SNIPPET}
        lang="ts"
        hint={t("sdk.ts.tracedHint")}
      />

      <GuideCard
        title={t("sdk.ts.spanBlockTitle")}
        sub={t("sdk.ts.spanBlockSub")}
        options={strTuples(raw("sdk.ts.spanBlockOptions"))}
        snippet={TS_SPAN_BLOCK_SNIPPET}
        lang="ts"
        hint={t("sdk.ts.spanBlockHint")}
      />

      <GuideCard
        title={t("sdk.ts.spanTagTitle")}
        sub={t("sdk.ts.spanTagSub")}
        options={strTuples(raw("sdk.ts.spanTagOptions"))}
        snippet={TS_SPAN_TAG_SNIPPET}
        lang="ts"
        hint={t("sdk.ts.spanTagHint")}
      />
    </>
  );
}

function OtlpPanel({ baseUrl, orgName }: { baseUrl: string; orgName: string }) {
  const { t, raw, tsub } = useI18n();
  const httpKv = strTuples(raw("sdk.otlp.httpKv"));
  return (
    <>
      <section className="eo-card">
        <div className="eo-card-h">
          <h3 className="eo-card-title">{t("sdk.otlp.endpointTitle")}</h3>
          <span className="eo-card-sub">{t("sdk.otlp.endpointSub")}</span>
          <div className="eo-card-actions">
            <CopyBtn
              text={curlSnippet(TOKEN_PLACEHOLDER, baseUrl)}
              label={t("sdk.otlp.copyCurl")}
            />
          </div>
        </div>
        <dl className="eo-kv-list">
          <div className="eo-kv-row">
            <dt>{t("sdk.otlp.urlStandard")}</dt>
            <dd>
              <code>
                {baseUrl}
                {PATH_OTEL_STD}
              </code>
              <CopyBtn text={`${baseUrl}${PATH_OTEL_STD}`} />
            </dd>
          </div>
          <div className="eo-kv-row">
            <dt>{t("sdk.otlp.urlLegacy")}</dt>
            <dd>
              <code>
                {baseUrl}
                {PATH_EASYOBS}
              </code>
              <CopyBtn text={`${baseUrl}${PATH_EASYOBS}`} />
            </dd>
          </div>
          {httpKv.map(([k, v]) => (
            <div className="eo-kv-row" key={k}>
              <dt>{k}</dt>
              <dd>
                <code>{v}</code>
              </dd>
            </div>
          ))}
        </dl>
        <div className="eo-divider" />
        <CodeBlock src={curlSnippet(TOKEN_PLACEHOLDER, baseUrl)} lang="sh" />
        <p className="eo-hint">
          {tsub("sdk.otlp.endpointFoot", { base: baseUrl, org: orgName })}
        </p>
      </section>

      <section className="eo-card">
        <div className="eo-card-h">
          <h3 className="eo-card-title">{t("sdk.otlp.sampleBodyTitle")}</h3>
          <span className="eo-card-sub">{t("sdk.otlp.jsonSub")}</span>
          <div className="eo-card-actions">
            <CopyBtn text={RAW_JSON_SAMPLE} label={t("sdk.otlp.copyJson")} />
          </div>
        </div>
        <CodeBlock src={RAW_JSON_SAMPLE} lang="json" />
        <p className="eo-hint">
          {t("sdk.otlp.jsonHint")}{" "}
          <a
            href="https://github.com/open-telemetry/opentelemetry-proto/blob/main/opentelemetry/proto/trace/v1/trace.proto"
            target="_blank"
            rel="noreferrer"
          >
            trace.proto
          </a>
        </p>
      </section>

      <section className="eo-card">
        <div className="eo-card-h">
          <h3 className="eo-card-title">{t("sdk.otlp.otherTitle")}</h3>
          <span className="eo-card-sub">{t("sdk.otlp.otherSub")}</span>
        </div>
        <dl className="eo-kv-list eo-kv-list-code">
          <div className="eo-kv-row">
            <dt>Java</dt>
            <dd>
              io.opentelemetry:opentelemetry-exporter-otlp →{" "}
              <code>OtlpHttpSpanExporter.builder().setEndpoint(...)</code>
            </dd>
          </div>
          <div className="eo-kv-row">
            <dt>Go</dt>
            <dd>
              go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp
            </dd>
          </div>
          <div className="eo-kv-row">
            <dt>.NET</dt>
            <dd>OpenTelemetry.Exporter.OpenTelemetryProtocol (HTTP)</dd>
          </div>
          <div className="eo-kv-row">
            <dt>Rust</dt>
            <dd>opentelemetry-otlp (with the “http-proto” feature)</dd>
          </div>
          <div className="eo-kv-row">
            <dt>Ruby / PHP / Erlang</dt>
            <dd>any opentelemetry-exporter-otlp-http package</dd>
          </div>
        </dl>
        <p className="eo-hint">{t("sdk.otlp.otherHint")}</p>
      </section>
    </>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type TabId = "py" | "ts" | "otlp";

export default function SdkPage() {
  const auth = useAuth();
  const { t } = useI18n();
  const base = apiBase();
  const [tab, setTab] = useState<TabId>("py");
  const orgName = auth.currentOrg?.name ?? "(org)";
  const tabs: Array<{ id: TabId; label: string; sub: string }> = [
    { id: "py", label: t("sdk.tabs.py"), sub: t("sdk.tabs.pySub") },
    { id: "ts", label: t("sdk.tabs.ts"), sub: t("sdk.tabs.tsSub") },
    { id: "otlp", label: t("sdk.tabs.otlp"), sub: t("sdk.tabs.otlpSub") },
  ];

  return (
    <>
      <div className="eo-page-head">
        <div>
          <h1 className="eo-page-title">{t("sdk.title")}</h1>
          <p className="eo-page-lede">{t("sdk.lede")}</p>
        </div>
      </div>

      <div className="eo-tab-bar" role="tablist" aria-label={t("sdk.ariaTabs")}>
        {tabs.map((tb) => (
          <button
            key={tb.id}
            type="button"
            role="tab"
            aria-selected={tab === tb.id}
            data-active={tab === tb.id ? "true" : "false"}
            className="eo-tab"
            onClick={() => setTab(tb.id)}
            title={tb.sub}
          >
            {tb.label}
          </button>
        ))}
      </div>

      {tab === "py" && <PythonPanel baseUrl={base} orgName={orgName} />}
      {tab === "ts" && <TsJsPanel baseUrl={base} orgName={orgName} />}
      {tab === "otlp" && <OtlpPanel baseUrl={base} orgName={orgName} />}
    </>
  );
}
