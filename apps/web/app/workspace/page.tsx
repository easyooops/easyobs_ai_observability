"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlarmPinned } from "@/components/alarm-pinned";
import { Donut, PercentileBars, Sparkline, StackedBars } from "@/components/charts";
import { fetchOverview } from "@/lib/api";
import { rangeKey, resolveRange, useWorkspace, windowLabel } from "@/lib/context";
import { fmtInt, fmtMs, fmtPct, fmtPrice, fmtRel, fmtTokens } from "@/lib/format";
import { useI18n } from "@/lib/i18n/context";
import { OverviewQualityPanel } from "./overview-quality-panel";
import { QualityGuard } from "./quality/guard";

export default function OverviewPage() {
  const { t, tsub } = useI18n();
  const ws = useWorkspace();
  const { live } = ws;
  const range = resolveRange(ws);
  const rk = rangeKey(ws);
  const wLabel = windowLabel(ws);
  const q = useQuery({
    queryKey: ["overview", rk],
    queryFn: () => fetchOverview(range, 24),
    refetchInterval: live ? 5_000 : false,
  });

  const data = q.data;
  const series = data?.series;
  const kpi = data?.kpi;

  const axisLabels = series
    ? (() => {
        const start = new Date(series.startedAt).getTime();
        const span = series.bucketSpanSec * 1000;
        return Array.from({ length: series.bucketCount }, (_, i) => {
          const d = new Date(start + i * span);
          return `${d.getUTCHours()}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
        });
      })()
    : [];

  const statusSlices = data
    ? [
        { label: t("pages.overview.statusOk"), value: data.statusMix.OK, color: "#22c07a" },
        { label: t("pages.overview.statusError"), value: data.statusMix.ERROR, color: "#e3465e" },
        { label: t("pages.overview.statusUnset"), value: data.statusMix.UNSET, color: "#c7d1de" },
      ]
    : [];

  const latencyBandMax = Math.max(1, ...(data?.latencyBands?.map((b) => b.count) ?? [1]));

  return (
    <>
      <div className="eo-page-head">
        <div>
          <h1 className="eo-page-title">{t("pages.overview.title")}</h1>
          <p className="eo-page-lede">{t("pages.overview.lede")}</p>
        </div>
        <div className="eo-page-meta">
          <span className="eo-tag">
            {t("pages.overview.window")}: {wLabel}
          </span>
          <span className="eo-tag eo-tag-accent">
            {t("pages.overview.updated")} {data ? fmtRel(data.generatedAt) : t("pages.overview.dash")}
          </span>
          <Link href="/workspace/tracing/" className="eo-btn eo-btn-primary">
            {t("pages.overview.exploreTraces")}
          </Link>
        </div>
      </div>

      <h2 className="eo-page-title" style={{ fontSize: 20, margin: "8px 0 4px" }}>
        {t("pages.overview.observeSectionTitle")}
      </h2>

      <AlarmPinned surface="workspace_overview" title="Pinned alarms" />

      {q.isLoading && <div className="eo-empty">{t("pages.overview.loading")}</div>}
      {q.isError && <div className="eo-empty">{t("pages.overview.error")}</div>}

      {data && (
        <>
          <div className="eo-kpi-grid">
            <article className="eo-kpi" data-tone="ink">
              <span className="eo-kpi-label">{t("pages.overview.kpiTotalTraces")}</span>
              <strong className="eo-kpi-value">{fmtInt(kpi?.totalTraces)}</strong>
              <span className="eo-kpi-meta">
                {tsub("pages.overview.kpiTotalMeta", {
                  services: String(kpi?.uniqueServices ?? 0),
                  window: wLabel,
                })}
              </span>
              <div className="eo-kpi-spark" style={{ width: 100 }}>
                <Sparkline values={series?.total ?? []} tone="ink" />
              </div>
            </article>

            <article className="eo-kpi" data-tone="err">
              <span className="eo-kpi-label">{t("pages.overview.kpiErrorTraces")}</span>
              <strong className="eo-kpi-value">{fmtInt(kpi?.errorTraces)}</strong>
              <span className="eo-kpi-meta">
                {tsub("pages.overview.kpiErrorMeta", { rate: fmtPct(kpi?.errorRate ?? 0) })}
              </span>
              <div className="eo-kpi-spark" style={{ width: 100 }}>
                <Sparkline values={series?.errors ?? []} tone="err" />
              </div>
            </article>

            <article className="eo-kpi">
              <span className="eo-kpi-label">{t("pages.overview.kpiLatency")}</span>
              <strong className="eo-kpi-value">
                {fmtMs(kpi?.p50LatencyMs)} <span className="eo-muted" style={{ fontSize: 13 }}>·</span>{" "}
                {fmtMs(kpi?.p95LatencyMs)}
              </strong>
              <span className="eo-kpi-meta">
                {tsub("pages.overview.kpiLatencyMeta", {
                  p90: fmtMs(kpi?.p90LatencyMs),
                  p99: fmtMs(kpi?.p99LatencyMs),
                })}
              </span>
              <div className="eo-kpi-spark" style={{ width: 100 }}>
                <Sparkline values={series?.p95Ms ?? []} />
              </div>
            </article>

            <article className="eo-kpi" data-tone="warn">
              <span className="eo-kpi-label">{t("pages.overview.kpiThroughput")}</span>
              <strong className="eo-kpi-value">
                {kpi && kpi.totalTraces > 0
                  ? `${(kpi.totalTraces / Math.max(1, data?.windowHours ?? 1)).toFixed(1)}${t("pages.overview.perHour")}`
                  : t("pages.overview.dash")}
              </strong>
              <span className="eo-kpi-meta">
                {tsub("pages.overview.kpiThroughputMeta", {
                  ok: fmtInt(kpi?.okTraces),
                  unset: fmtInt(kpi?.unsetTraces),
                })}
              </span>
            </article>
          </div>

          <div className="eo-grid-2">
            <div className="eo-card">
              <div className="eo-card-h">
                <h3 className="eo-card-title">
                  <span className="eo-chip-dot" />
                  {t("pages.overview.chartTraceVolume")}
                </h3>
                <span className="eo-card-sub">
                  {tsub("pages.overview.chartTraceSub", {
                    buckets: String(series?.bucketCount ?? 0),
                    mins: String((series?.bucketSpanSec ?? 0) / 60 | 0),
                  })}
                </span>
              </div>
              <StackedBars
                totals={series?.total ?? []}
                errors={series?.errors ?? []}
                labels={axisLabels}
              />
            </div>

            <div className="eo-card">
              <div className="eo-card-h">
                <h3 className="eo-card-title">{t("pages.overview.chartLatencyPct")}</h3>
                <span className="eo-card-sub">
                  {t("pages.overview.window")} {wLabel}
                </span>
              </div>
              <PercentileBars
                values={[
                  { key: "P50", value: kpi?.p50LatencyMs ?? 0 },
                  { key: "P90", value: kpi?.p90LatencyMs ?? 0 },
                  { key: "P95", value: kpi?.p95LatencyMs ?? 0 },
                  { key: "P99", value: kpi?.p99LatencyMs ?? 0 },
                ]}
              />
              <div className="eo-divider" style={{ margin: "10px 0" }} />
              <div className="eo-card-sub" style={{ marginBottom: 6 }}>
                {t("pages.overview.durationDist")}
              </div>
              <div className="eo-dist">
                {data.latencyBands.map((b) => {
                  const w = Math.round((b.count / latencyBandMax) * 100);
                  return (
                    <div key={b.label} className="eo-dist-row">
                      <span>{b.label}</span>
                      <div className="eo-dist-bar">
                        <i style={{ width: `${w}%` }} />
                      </div>
                      <span className="mono">{fmtInt(b.count)}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {data.llm && (
            <div className="eo-kpi-grid" style={{ marginTop: 12 }}>
              <article className="eo-kpi" data-tone="ink">
                <span className="eo-kpi-label">{t("pages.overview.kpiLlmCalls")}</span>
                <strong className="eo-kpi-value">{fmtInt(data.llm.llmCalls)}</strong>
                <span className="eo-kpi-meta">
                  {tsub("pages.overview.kpiLlmMeta", {
                    retrieve: String(data.llm.retrieveCalls),
                    tool: String(data.llm.toolCalls),
                  })}
                </span>
              </article>
              <article className="eo-kpi">
                <span className="eo-kpi-label">{t("pages.overview.kpiTokens")}</span>
                <strong className="eo-kpi-value">
                  {fmtTokens(data.llm.tokensIn)}{" "}
                  <span className="eo-muted" style={{ fontSize: 13 }}>·</span>{" "}
                  {fmtTokens(data.llm.tokensOut)}
                </strong>
                <span className="eo-kpi-meta">
                  {tsub("pages.overview.kpiTokensMeta", {
                    total: fmtTokens(data.llm.tokensTotal),
                  })}
                </span>
              </article>
              <article className="eo-kpi" data-tone="warn">
                <span className="eo-kpi-label">{t("pages.overview.kpiEstCost")}</span>
                <strong className="eo-kpi-value">{fmtPrice(data.llm.price)}</strong>
                <span className="eo-kpi-meta">
                  {tsub("pages.overview.kpiCostMeta", {
                    n: String(data.llm.tracesWithLlm),
                  })}
                </span>
              </article>
              <article className="eo-kpi">
                <span className="eo-kpi-label">{t("pages.overview.kpiSessions")}</span>
                <strong className="eo-kpi-value">{fmtInt(data.llm.uniqueSessions)}</strong>
                <span className="eo-kpi-meta">{t("pages.overview.kpiSessionsMeta")}</span>
              </article>
            </div>
          )}

          {data.llm && (data.llm.topModels.length > 0 || data.llm.topSteps.length > 0) && (
            <div className="eo-grid-3">
              <div className="eo-card">
                <div className="eo-card-h">
                  <h3 className="eo-card-title">{t("pages.overview.topModels")}</h3>
                  <span className="eo-card-sub">{t("pages.overview.topModelsSub")}</span>
                </div>
                <div className="eo-dist">
                  {data.llm.topModels.length === 0 && (
                    <div className="eo-empty">{t("pages.overview.noModelTags")}</div>
                  )}
                  {(() => {
                    const m = Math.max(1, ...data.llm.topModels.map((x) => x.count));
                    return data.llm.topModels.map((x) => (
                      <div key={x.name} className="eo-dist-row">
                        <span title={x.name}>{x.name}</span>
                        <div className="eo-dist-bar">
                          <i style={{ width: `${Math.round((x.count / m) * 100)}%` }} />
                        </div>
                        <span className="mono">{fmtInt(x.count)}</span>
                      </div>
                    ));
                  })()}
                </div>
              </div>

              <div className="eo-card">
                <div className="eo-card-h">
                  <h3 className="eo-card-title">{t("pages.overview.pipelineSteps")}</h3>
                  <span className="eo-card-sub">{t("pages.overview.pipelineStepsSub")}</span>
                </div>
                <div className="eo-dist">
                  {data.llm.topSteps.length === 0 && (
                    <div className="eo-empty">{t("pages.overview.noStepLabels")}</div>
                  )}
                  {(() => {
                    const m = Math.max(1, ...data.llm.topSteps.map((x) => x.count));
                    return data.llm.topSteps.map((x) => (
                      <div key={x.name} className="eo-dist-row">
                        <span title={x.name}>{x.name}</span>
                        <div className="eo-dist-bar">
                          <i style={{ width: `${Math.round((x.count / m) * 100)}%` }} />
                        </div>
                        <span className="mono">{fmtInt(x.count)}</span>
                      </div>
                    ));
                  })()}
                </div>
              </div>

              <div className="eo-card">
                <div className="eo-card-h">
                  <h3 className="eo-card-title">{t("pages.overview.vendors")}</h3>
                  <span className="eo-card-sub">{t("pages.overview.vendorsSub")}</span>
                </div>
                <div className="eo-dist">
                  {data.llm.topVendors.length === 0 && (
                    <div className="eo-empty">{t("pages.overview.noVendorTags")}</div>
                  )}
                  {(() => {
                    const m = Math.max(1, ...data.llm.topVendors.map((x) => x.count));
                    return data.llm.topVendors.map((x) => (
                      <div key={x.name} className="eo-dist-row">
                        <span title={x.name}>{x.name}</span>
                        <div className="eo-dist-bar">
                          <i style={{ width: `${Math.round((x.count / m) * 100)}%` }} />
                        </div>
                        <span className="mono">{fmtInt(x.count)}</span>
                      </div>
                    ));
                  })()}
                </div>
              </div>
            </div>
          )}

          <div className="eo-grid-3">
            <div className="eo-card">
              <div className="eo-card-h">
                <h3 className="eo-card-title">{t("pages.overview.serviceActivity")}</h3>
                <span className="eo-card-sub">
                  {tsub("pages.overview.serviceActivitySub", {
                    n: String(data.services.length),
                  })}
                </span>
              </div>
              <div className="eo-table-wrap">
                <table className="eo-table">
                  <thead>
                    <tr>
                      <th>{t("pages.overview.colService")}</th>
                      <th>{t("pages.overview.colTraces")}</th>
                      <th>{t("pages.overview.colErrPct")}</th>
                      <th>{t("pages.overview.colP50")}</th>
                      <th>{t("pages.overview.colP95")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.services.length === 0 && (
                      <tr>
                        <td colSpan={5}>
                          <div className="eo-empty">{t("pages.overview.noServicesWindow")}</div>
                        </td>
                      </tr>
                    )}
                    {data.services.map((s) => (
                      <tr key={s.name}>
                        <td className="eo-td-name">{s.name}</td>
                        <td className="mono">{fmtInt(s.count)}</td>
                        <td>
                          <span className="eo-status" data-tone={s.errorRate > 0 ? "err" : "ok"}>
                            {fmtPct(s.errorRate)}
                          </span>
                        </td>
                        <td className="mono">{fmtMs(s.p50)}</td>
                        <td className="mono">{fmtMs(s.p95)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="eo-card">
              <div className="eo-card-h">
                <h3 className="eo-card-title">{t("pages.overview.statusMix")}</h3>
                <span className="eo-card-sub">{t("pages.overview.statusMixSub")}</span>
              </div>
              <Donut slices={statusSlices} />
            </div>

            <div className="eo-card">
              <div className="eo-card-h">
                <h3 className="eo-card-title">{t("pages.overview.topOperations")}</h3>
                <span className="eo-card-sub">{t("pages.overview.topOperationsSub")}</span>
              </div>
              <div className="eo-dist">
                {data.topOperations.length === 0 && (
                  <div className="eo-empty">{t("pages.overview.noOperations")}</div>
                )}
                {data.topOperations.map((op) => {
                  const m = Math.max(
                    1,
                    ...data.topOperations.map((o) => o.count),
                  );
                  const pct = Math.round((op.count / m) * 100);
                  return (
                    <div
                      key={op.name}
                      className="eo-dist-row"
                      style={{ gridTemplateColumns: "1fr 1fr 90px" }}
                    >
                      <span title={op.name}>{op.name}</span>
                      <div className="eo-dist-bar">
                        <i style={{ width: `${pct}%` }} />
                      </div>
                      <span className="mono" style={{ textAlign: "right" }}>
                        {fmtInt(op.count)} · {fmtMs(op.p95)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </>
      )}

      <QualityGuard>
        <OverviewQualityPanel />
      </QualityGuard>
    </>
  );
}
