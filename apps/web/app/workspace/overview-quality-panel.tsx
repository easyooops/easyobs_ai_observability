"use client";

import Link from "next/link";
import { Fragment, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchEvalProfiles, fetchQualityOverview, type EvalRun } from "@/lib/api";
import { fmtInt, fmtPct, fmtPrice, fmtRel } from "@/lib/format";
import { useI18n } from "@/lib/i18n/context";

function statusTone(status: string): "ok" | "err" | "warn" {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "err";
  return "warn";
}

function laneLabel(lane: string, t: (k: string) => string): string {
  switch (lane) {
    case "rule_auto":
      return t("pages.quality.laneAutoRule");
    case "judge_manual":
      return t("pages.quality.laneManual");
    case "judge_schedule":
      return t("pages.quality.laneScheduled");
    default:
      return lane;
  }
}

function FeatureCard({
  title,
  body,
  href,
  cta,
}: {
  title: string;
  body: string;
  href: string;
  cta: string;
}) {
  return (
    <div className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">{title}</h3>
      </div>
      <p style={{ fontSize: 13, color: "var(--eo-mute)" }}>{body}</p>
      <div style={{ marginTop: 10 }}>
        <Link href={href} className="eo-btn eo-btn-ghost">
          {cta}
        </Link>
      </div>
    </div>
  );
}

/** Evaluation summary embedded on the unified workspace overview. */
export function OverviewQualityPanel() {
  const { t, tsub } = useI18n();
  const q = useQuery({
    queryKey: ["quality", "overview"],
    queryFn: fetchQualityOverview,
    refetchInterval: 15_000,
  });
  const profilesQ = useQuery({
    queryKey: ["eval", "profiles"],
    queryFn: () => fetchEvalProfiles(true),
    staleTime: 60_000,
  });
  const data = q.data;
  const kpi = data?.kpi;

  const profileName = (id: string | null | undefined) => {
    if (!id) return t("pages.quality.noProfile");
    const p = profilesQ.data?.find((x) => x.id === id);
    return p?.name ?? id.slice(0, 8);
  };

  const runsByProfile = useMemo(() => {
    const list = data?.recentRuns ?? [];
    const m = new Map<string, EvalRun[]>();
    for (const r of list) {
      const key = r.profileId ?? "—";
      const arr = m.get(key) ?? [];
      arr.push(r);
      m.set(key, arr);
    }
    for (const [, rows] of m) {
      rows.sort(
        (a, b) =>
          new Date(b.startedAt).getTime() - new Date(a.startedAt).getTime(),
      );
    }
    return [...m.entries()].sort((a, b) => {
      const ta = a[1][0] ? new Date(a[1][0].startedAt).getTime() : 0;
      const tb = b[1][0] ? new Date(b[1][0].startedAt).getTime() : 0;
      return tb - ta;
    });
  }, [data?.recentRuns]);

  return (
    <section className="eo-overview-quality" aria-labelledby="overview-quality-heading">
      <h2 id="overview-quality-heading" className="eo-page-title" style={{ fontSize: 20, margin: "8px 0 4px" }}>
        {t("pages.overview.qualitySectionTitle")}
      </h2>
      <p className="eo-page-lede">
        {t("pages.overview.qualitySectionLede")}
      </p>

      {q.isLoading && <div className="eo-empty">{t("pages.quality.loading")}</div>}
      {q.isError && <div className="eo-empty">{t("pages.quality.error")}</div>}

      {data && (
        <>
          <div className="eo-kpi-grid" style={{ marginTop: 16 }}>
            <article className="eo-kpi" data-tone="ink">
              <span className="eo-kpi-label">{t("pages.quality.kpiProfiles")}</span>
              <strong className="eo-kpi-value">{fmtInt(kpi?.profileCount)}</strong>
              <span className="eo-kpi-meta">
                {fmtInt(kpi?.judgeModelCount)} {t("pages.quality.kpiProfilesMeta")}
              </span>
            </article>
            <article className="eo-kpi">
              <span className="eo-kpi-label">{t("pages.quality.kpiAvgScore")}</span>
              <strong className="eo-kpi-value">
                {kpi ? kpi.avgScore.toFixed(3) : t("pages.overview.dash")}
              </strong>
              <span className="eo-kpi-meta">
                {tsub("pages.quality.passRateKpiMeta", {
                  rate: fmtPct((kpi?.passRate ?? 0) * 100),
                })}
              </span>
            </article>
            <article className="eo-kpi" data-tone="warn">
              <span className="eo-kpi-label">{t("pages.quality.kpiOpenImp")}</span>
              <strong className="eo-kpi-value">{fmtInt(kpi?.openImprovements)}</strong>
              <span className="eo-kpi-meta">
                <Link href="/workspace/quality/improvements/" className="eo-link">
                  {t("pages.quality.triageLink")}
                </Link>
              </span>
            </article>
            <article className="eo-kpi" data-tone="err">
              <span className="eo-kpi-label">{t("pages.quality.costTitle")}</span>
              <strong className="eo-kpi-value">{fmtPrice(data.cost.monthCostUsd)}</strong>
              <span className="eo-kpi-meta">
                {tsub("pages.quality.kpiCostCallsMeta", {
                  calls: fmtInt(data.cost.judgeCalls),
                  ruleEvals: fmtInt(data.cost.ruleEvals),
                })}
              </span>
            </article>
          </div>

          <div className="eo-grid-2" style={{ marginTop: 16 }}>
            <div className="eo-card">
              <div className="eo-card-h">
                <h3 className="eo-card-title">{t("pages.quality.recentRuns")}</h3>
                <span className="eo-card-sub">
                  {tsub("pages.quality.recentRunsSub", {
                    n: fmtInt(kpi?.autoRuleRunsLast20),
                  })}
                </span>
              </div>
              <div className="eo-table-wrap">
                <table className="eo-table">
                  <thead>
                    <tr>
                      <th>{t("pages.quality.colRun")}</th>
                      <th>{t("pages.quality.colLane")}</th>
                      <th>{t("pages.quality.colSubjects")}</th>
                      <th>{t("pages.quality.colPass")}</th>
                      <th>{t("pages.quality.colCost")}</th>
                      <th>{t("pages.quality.colStarted")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recentRuns.length === 0 && (
                      <tr>
                        <td colSpan={6}>
                          <div className="eo-empty">{t("pages.quality.noRunsYet")}</div>
                        </td>
                      </tr>
                    )}
                    {runsByProfile.map(([profileKey, runList]) => (
                      <Fragment key={profileKey === "—" ? "_noprofile" : profileKey}>
                        <tr style={{ background: "var(--eo-bg-2)" }}>
                          <td colSpan={6} style={{ fontSize: 12 }}>
                            <strong>{profileName(profileKey === "—" ? null : profileKey)}</strong>
                            <span className="eo-mute">
                              {" "}
                              ·{" "}
                              {tsub(
                                runList.length === 1
                                  ? "pages.quality.runCountOne"
                                  : "pages.quality.runCountMany",
                                { n: String(runList.length) },
                              )}
                            </span>
                          </td>
                        </tr>
                        {runList.map((r) => (
                          <tr key={r.id}>
                            <td>
                              <Link
                                href={`/workspace/quality/runs/?run=${encodeURIComponent(r.id)}`}
                                className="eo-link mono"
                              >
                                {r.id.slice(0, 8)}
                              </Link>
                            </td>
                            <td>
                              <span className="eo-pill-role" data-role="DV">
                                {laneLabel(r.triggerLane, t)}
                              </span>
                            </td>
                            <td className="mono">{fmtInt(r.subjectCount)}</td>
                            <td>
                              <span className="eo-status" data-tone={statusTone(r.status)}>
                                {fmtPct(r.passRate * 100)}
                              </span>
                            </td>
                            <td className="mono">{fmtPrice(r.costActualUsd)}</td>
                            <td>{fmtRel(r.startedAt)}</td>
                          </tr>
                        ))}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="eo-card">
              <div className="eo-card-h">
                <h3 className="eo-card-title">{t("pages.quality.costComposition")}</h3>
                <span className="eo-card-sub">{t("pages.quality.costCompositionSub")}</span>
              </div>
              <div className="eo-dist">
                <div className="eo-dist-row">
                  <span>{t("pages.quality.costJudgeIn")}</span>
                  <div className="eo-dist-bar">
                    <i style={{ width: "100%" }} />
                  </div>
                  <span className="mono">{fmtInt(data.cost.judgeInputTokens)}</span>
                </div>
                <div className="eo-dist-row">
                  <span>{t("pages.quality.costJudgeOut")}</span>
                  <div className="eo-dist-bar">
                    <i
                      style={{
                        width: `${
                          data.cost.judgeInputTokens > 0
                            ? Math.min(
                                100,
                                Math.round(
                                  (data.cost.judgeOutputTokens / data.cost.judgeInputTokens) * 100,
                                ),
                              )
                            : 0
                        }%`,
                      }}
                    />
                  </div>
                  <span className="mono">{fmtInt(data.cost.judgeOutputTokens)}</span>
                </div>
                <div className="eo-dist-row">
                  <span>{t("pages.quality.costRuleEvals")}</span>
                  <div className="eo-dist-bar">
                    <i
                      style={{
                        width: `${
                          data.cost.judgeCalls + data.cost.ruleEvals > 0
                            ? Math.round(
                                (data.cost.ruleEvals /
                                  (data.cost.judgeCalls + data.cost.ruleEvals)) *
                                  100,
                              )
                            : 0
                        }%`,
                      }}
                    />
                  </div>
                  <span className="mono">{fmtInt(data.cost.ruleEvals)}</span>
                </div>
              </div>
              <div className="eo-divider" style={{ margin: "10px 0" }} />
              <p className="eo-mute" style={{ fontSize: 12 }}>
                {t("pages.quality.costFootnote")}
              </p>
            </div>
          </div>

          <div className="eo-grid-4" style={{ marginTop: 16 }}>
            <FeatureCard
              title={t("pages.quality.featProfileTitle")}
              body={t("pages.quality.featProfileBody")}
              href="/workspace/quality/profiles/"
              cta={t("pages.quality.newProfile")}
            />
            <FeatureCard
              title={t("pages.quality.featConsensusTitle")}
              body={t("pages.quality.featConsensusBody")}
              href="/workspace/quality/judges/"
              cta={t("pages.quality.featConsensusCta")}
            />
            <FeatureCard
              title={t("pages.quality.featGoldenTitle")}
              body={t("pages.quality.featGoldenBody")}
              href="/workspace/quality/golden/"
              cta={t("pages.quality.featGoldenCta")}
            />
            <FeatureCard
              title={t("pages.quality.featCostTitle")}
              body={t("pages.quality.featCostBody")}
              href="/workspace/quality/profiles/"
              cta={t("pages.quality.featCostCta")}
            />
          </div>
        </>
      )}
    </section>
  );
}
