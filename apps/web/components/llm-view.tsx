"use client";

import type { SpanLlm, TraceLlmSummary } from "@/lib/api";
import { fmtPrice, fmtScore, fmtTokens, truncate } from "@/lib/format";

export function SpanLlmPanel({ llm }: { llm: SpanLlm | undefined | null }) {
  if (!llm) return null;
  const empty =
    !llm.kind &&
    !llm.query &&
    !llm.response &&
    !llm.model &&
    !llm.tool &&
    !llm.tokensTotal &&
    !llm.docsCount;
  if (empty) {
    return (
      <div className="eo-empty" style={{ padding: "10px 8px" }}>
        No LLM annotations on this span
      </div>
    );
  }

  return (
    <div className="eo-llm-panel">
      {(llm.kind || llm.step || llm.model || llm.vendor) && (
        <div className="eo-llm-badges">
          {llm.kind && <span className="eo-badge" data-kind={llm.kind}>{llm.kind}</span>}
          {llm.step && <span className="eo-chip">{llm.step}</span>}
          {llm.model && <span className="eo-chip eo-chip-accent">{llm.model}</span>}
          {llm.vendor && <span className="eo-chip">{llm.vendor}</span>}
          {llm.tool && <span className="eo-chip">tool: {llm.tool}</span>}
          {llm.verdict && (
            <span
              className="eo-chip"
              data-tone={llm.verdict === "pass" ? "ok" : llm.verdict === "retry" ? "warn" : "err"}
            >
              verdict: {llm.verdict}
            </span>
          )}
          {typeof llm.attempt === "number" && llm.attempt > 0 && (
            <span className="eo-chip">attempt #{llm.attempt}</span>
          )}
        </div>
      )}

      {(llm.tokensTotal > 0 || llm.price > 0 || llm.docsCount > 0 || llm.score !== null) && (
        <div className="eo-llm-metrics">
          {llm.tokensIn > 0 && (
            <div className="eo-llm-metric">
              <span className="eo-llm-metric-k">Tokens in</span>
              <span className="eo-llm-metric-v">{fmtTokens(llm.tokensIn)}</span>
            </div>
          )}
          {llm.tokensOut > 0 && (
            <div className="eo-llm-metric">
              <span className="eo-llm-metric-k">Tokens out</span>
              <span className="eo-llm-metric-v">{fmtTokens(llm.tokensOut)}</span>
            </div>
          )}
          {llm.tokensTotal > 0 && (
            <div className="eo-llm-metric">
              <span className="eo-llm-metric-k">Tokens total</span>
              <span className="eo-llm-metric-v">{fmtTokens(llm.tokensTotal)}</span>
            </div>
          )}
          {llm.price > 0 && (
            <div className="eo-llm-metric">
              <span className="eo-llm-metric-k">Cost</span>
              <span className="eo-llm-metric-v">{fmtPrice(llm.price)}</span>
            </div>
          )}
          {llm.docsCount > 0 && (
            <div className="eo-llm-metric">
              <span className="eo-llm-metric-k">Docs</span>
              <span className="eo-llm-metric-v">
                {llm.docsCount}
                {typeof llm.docsTopScore === "number" && (
                  <small style={{ opacity: 0.7, marginLeft: 6 }}>
                    top {fmtScore(llm.docsTopScore)}
                  </small>
                )}
              </span>
            </div>
          )}
          {typeof llm.score === "number" && (
            <div className="eo-llm-metric">
              <span className="eo-llm-metric-k">Score</span>
              <span className="eo-llm-metric-v">{fmtScore(llm.score)}</span>
            </div>
          )}
        </div>
      )}

      {llm.query && (
        <section className="eo-llm-block">
          <header>
            <span>Input</span>
            <small>{llm.query.length} chars</small>
          </header>
          <pre>{llm.query}</pre>
        </section>
      )}

      {llm.response && (
        <section className="eo-llm-block" data-tone="ok">
          <header>
            <span>Output</span>
            <small>{llm.response.length} chars</small>
          </header>
          <pre>{llm.response}</pre>
        </section>
      )}

      {llm.tool && (llm.response === null || !llm.response) && (
        <section className="eo-llm-block">
          <header>
            <span>Tool</span>
            <small>{llm.tool}</small>
          </header>
          <pre>{"(see Attributes tab for tool in/out)"}</pre>
        </section>
      )}

      {llm.docsRaw && <DocsPreview raw={llm.docsRaw} />}
    </div>
  );
}

function DocsPreview({ raw }: { raw: string }) {
  let docs: Array<{ id?: string; score?: number; snippet?: string }> = [];
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) docs = parsed as typeof docs;
  } catch {
    return (
      <section className="eo-llm-block">
        <header>
          <span>Retrieved docs</span>
          <small>raw</small>
        </header>
        <pre>{truncate(raw, 420)}</pre>
      </section>
    );
  }
  if (!docs.length) return null;
  return (
    <section className="eo-llm-block">
      <header>
        <span>Retrieved docs</span>
        <small>{docs.length}</small>
      </header>
      <ul className="eo-llm-docs">
        {docs.map((d, i) => (
          <li key={d.id ?? i}>
            <div className="eo-llm-doc-h">
              <strong>{d.id ?? `doc-${i}`}</strong>
              {typeof d.score === "number" && (
                <span className="eo-tag eo-tag-accent">{fmtScore(d.score)}</span>
              )}
            </div>
            {d.snippet && <p>{d.snippet}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function TraceLlmSummaryCard({
  summary,
}: {
  summary: TraceLlmSummary | undefined;
}) {
  if (!summary) return null;
  const hasAny =
    summary.tokensTotal > 0 ||
    summary.llmCalls > 0 ||
    summary.retrieveCalls > 0 ||
    summary.toolCalls > 0 ||
    summary.models.length > 0;
  if (!hasAny) return null;

  return (
    <div className="eo-llm-summary">
      <div className="eo-llm-summary-row">
        {summary.session && (
          <div className="eo-llm-summary-item">
            <span className="eo-llm-metric-k">Session</span>
            <span className="eo-llm-metric-v mono">{truncate(summary.session, 22)}</span>
          </div>
        )}
        {summary.user && (
          <div className="eo-llm-summary-item">
            <span className="eo-llm-metric-k">User</span>
            <span className="eo-llm-metric-v mono">{summary.user}</span>
          </div>
        )}
        {summary.request && (
          <div className="eo-llm-summary-item">
            <span className="eo-llm-metric-k">Request</span>
            <span className="eo-llm-metric-v mono">{summary.request}</span>
          </div>
        )}
        <div className="eo-llm-summary-item">
          <span className="eo-llm-metric-k">LLM / Retrieve / Tool</span>
          <span className="eo-llm-metric-v">
            {summary.llmCalls} / {summary.retrieveCalls} / {summary.toolCalls}
          </span>
        </div>
        <div className="eo-llm-summary-item">
          <span className="eo-llm-metric-k">Tokens</span>
          <span className="eo-llm-metric-v">
            {fmtTokens(summary.tokensIn)} in · {fmtTokens(summary.tokensOut)} out
          </span>
        </div>
        {summary.price > 0 && (
          <div className="eo-llm-summary-item">
            <span className="eo-llm-metric-k">Cost</span>
            <span className="eo-llm-metric-v">{fmtPrice(summary.price)}</span>
          </div>
        )}
        {summary.models.length > 0 && (
          <div className="eo-llm-summary-item">
            <span className="eo-llm-metric-k">Models</span>
            <span className="eo-llm-metric-v">
              {summary.models.map((m) => (
                <span key={m} className="eo-chip eo-chip-accent" style={{ marginRight: 4 }}>
                  {m}
                </span>
              ))}
            </span>
          </div>
        )}
      </div>

      {summary.query && (
        <section className="eo-llm-block">
          <header>
            <span>User query</span>
            <small>first captured</small>
          </header>
          <pre>{summary.query}</pre>
        </section>
      )}

      {summary.response && (
        <section className="eo-llm-block" data-tone="ok">
          <header>
            <span>Final response</span>
            <small>last captured</small>
          </header>
          <pre>{summary.response}</pre>
        </section>
      )}
    </div>
  );
}
