"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchImprovementPacks,
  fetchImprovements,
  updateImprovementStatus,
  type EffortLevel,
  type Improvement,
  type ImprovementProposal,
  type SecondaryCandidate,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n, type AppLocale } from "@/lib/i18n/context";
import { fmtPrice, fmtRel } from "@/lib/format";
import { ImprovementPackGuideContent } from "../guides";
import { canMutateQuality, QualityGuard, ScopeBanner, WriteHint } from "../guard";

const STATUS_OPTIONS = ["open", "accepted", "rejected"] as const;

const EFFORT_ORDER: EffortLevel[] = ["low", "medium", "high"];

/** Lower index = higher priority for the default sort. */
const EFFORT_RANK: Record<string, number> = {
  low: 0,
  medium: 1,
  high: 2,
};

/** Map any (non-strict) effort string to one of the three known buckets. */
function normalizeEffort(raw: string | undefined | null): EffortLevel {
  const v = (raw ?? "").toLowerCase();
  if (v === "low" || v === "medium" || v === "high") return v;
  return "medium";
}

export default function ImprovementsPage() {
  return (
    <QualityGuard>
      <Inner />
    </QualityGuard>
  );
}

function proposalTitle(p: ImprovementProposal, loc: AppLocale) {
  if (loc === "ko" && p.titleI18n?.ko) return p.titleI18n.ko;
  if (loc === "en" && p.titleI18n?.en) return p.titleI18n.en;
  return p.title;
}

function categoryLabel(p: ImprovementProposal, loc: AppLocale) {
  // Prefer the detailed (46-row) label when present; fall back to the
  // legacy 8-key label for older improvements stored before the catalog
  // migration.
  const detail = p.categoryDetailLabelI18n;
  if (detail) {
    if (loc === "ko" && detail.ko) return detail.ko;
    if (loc === "en" && detail.en) return detail.en;
  }
  if (loc === "ko" && p.categoryLabelI18n?.ko) return p.categoryLabelI18n.ko;
  if (loc === "en" && p.categoryLabelI18n?.en) return p.categoryLabelI18n.en;
  return p.categoryLabel ?? p.category;
}

function secondaryLabel(s: SecondaryCandidate, loc: AppLocale) {
  if (loc === "ko" && s.labelI18n?.ko) return s.labelI18n.ko;
  if (loc === "en" && s.labelI18n?.en) return s.labelI18n.en;
  return s.category;
}

function Inner() {
  const { t, locale } = useI18n();
  const auth = useAuth();
  const writable = canMutateQuality(auth);
  const qc = useQueryClient();
  const [filter, setFilter] = useState<string>("");
  const [pageTab, setPageTab] = useState<"pack" | "guide">("pack");
  const [effortFilter, setEffortFilter] = useState<EffortLevel | "">("");

  const packs = useQuery({
    queryKey: ["eval", "improvement-packs"],
    queryFn: fetchImprovementPacks,
    staleTime: 3600_000,
  });

  const list = useQuery({
    queryKey: ["eval", "improvements", filter],
    queryFn: () => fetchImprovements({ status: filter || undefined }),
  });

  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      updateImprovementStatus(id, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["eval", "improvements"] }),
  });

  const distribution = useMemo(() => {
    const counts: Record<EffortLevel, number> = { low: 0, medium: 0, high: 0 };
    for (const imp of list.data ?? []) {
      for (const p of imp.proposals) {
        counts[normalizeEffort(p.effort)] += 1;
      }
    }
    const total = counts.low + counts.medium + counts.high;
    return { counts, total };
  }, [list.data]);

  return (
    <>
      <div className="eo-page-head">
        <div>
          <h1 className="eo-page-title">{t("quality.improvements.title")}</h1>
          <p className="eo-page-lede">{t("quality.improvements.lede")}</p>
        </div>
        <div className="eo-page-meta">
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">{t("quality.improvements.allStatuses")}</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
      </div>
      <ScopeBanner />
      <WriteHint />

      <div className="eo-seg" aria-label="Improvement Pack view" style={{ marginBottom: 14 }}>
        <button
          type="button"
          data-active={pageTab === "pack"}
          onClick={() => setPageTab("pack")}
        >
          {t("quality.improvements.tabPack")}
        </button>
        <button
          type="button"
          data-active={pageTab === "guide"}
          onClick={() => setPageTab("guide")}
        >
          {t("quality.improvements.tabGuide")}
        </button>
      </div>

      {pageTab === "guide" && <ImprovementPackGuideContent />}

      {pageTab === "pack" && (
        <>
          {distribution.total > 0 && (
            <EffortDistributionSummary
              counts={distribution.counts}
              total={distribution.total}
              activeEffort={effortFilter}
              onSelect={(eff) => setEffortFilter((cur) => (cur === eff ? "" : eff))}
            />
          )}

          <div
            style={{
              display: "grid",
              gap: 10,
            }}
          >
            {list.isLoading && <div className="eo-empty">{t("quality.improvements.loading")}</div>}
            {list.data?.length === 0 && !list.isLoading && (
              <div className="eo-empty">{t("quality.improvements.empty")}</div>
            )}
            {list.data
              ?.filter((imp) =>
                !effortFilter ||
                imp.proposals.some((p) => normalizeEffort(p.effort) === effortFilter),
              )
              .map((imp) => (
                <Card
                  key={imp.id}
                  imp={imp}
                  packLabel={packIdToLabel(imp.improvementPack, packs.data ?? [], locale)}
                  writable={writable}
                  uiLocale={locale}
                  effortFilter={effortFilter}
                  onSetStatus={(status) => setStatus.mutate({ id: imp.id, status })}
                />
              ))}
          </div>
        </>
      )}
    </>
  );
}

function EffortDistributionSummary({
  counts,
  total,
  activeEffort,
  onSelect,
}: {
  counts: Record<EffortLevel, number>;
  total: number;
  activeEffort: EffortLevel | "";
  onSelect: (effort: EffortLevel) => void;
}) {
  const { t, tsub } = useI18n();
  const pct = (n: number) => (total === 0 ? 0 : Math.max(2, Math.round((n / total) * 100)));
  return (
    <div className="eo-card" style={{ marginBottom: 12 }}>
      <div className="eo-card-h">
        <h3 className="eo-card-title">{t("quality.improvements.effortBarTitle")}</h3>
        <span className="eo-card-sub">
          {tsub("quality.improvements.effortBarSub", { n: String(total) })}
        </span>
      </div>
      <div
        className="eo-effort-bar"
        role="img"
        aria-label={t("quality.improvements.effortBarTitle")}
        style={{ marginBottom: 8 }}
      >
        {EFFORT_ORDER.map((eff) =>
          counts[eff] > 0 ? (
            <i
              key={eff}
              data-effort={eff}
              style={{ width: `${pct(counts[eff])}%` }}
              title={`${eff}: ${counts[eff]}`}
            />
          ) : null,
        )}
      </div>
      <div className="eo-effort-legend" style={{ marginBottom: 10 }}>
        {EFFORT_ORDER.map((eff) => (
          <span key={eff} data-effort={eff}>
            {t(`quality.improvements.effort.${eff}`)} · {counts[eff]}
          </span>
        ))}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <span style={{ fontSize: 11, color: "var(--eo-mute)", alignSelf: "center" }}>
          {t("quality.improvements.effortFilterLabel")}:
        </span>
        {EFFORT_ORDER.map((eff) => (
          <button
            key={eff}
            type="button"
            className="eo-chip"
            data-active={activeEffort === eff}
            data-effort={eff}
            onClick={() => onSelect(eff)}
          >
            {t(`quality.improvements.effort.${eff}`)}
          </button>
        ))}
      </div>
    </div>
  );
}

function packIdToLabel(
  packId: string | null | undefined,
  packs: { id: string; label: string; labelI18n?: { en: string; ko: string } }[],
  loc: AppLocale,
): string {
  const id = packId || "easyobs_standard";
  const row = packs.find((p) => p.id === id);
  if (!row) return id;
  return (loc === "ko" ? row.labelI18n?.ko : row.labelI18n?.en) ?? row.label;
}

function Card({
  imp,
  packLabel,
  uiLocale,
  writable,
  effortFilter,
  onSetStatus,
}: {
  imp: Improvement;
  packLabel: string;
  uiLocale: AppLocale;
  writable: boolean;
  effortFilter: EffortLevel | "";
  onSetStatus: (status: string) => void;
}) {
  const { t } = useI18n();

  // Default sort: lowest effort first → operators see quick wins on top.
  // Within the same effort, fail (severity=high) is more urgent than warn.
  const sortedProposals = useMemo(() => {
    const filtered = effortFilter
      ? imp.proposals.filter((p) => normalizeEffort(p.effort) === effortFilter)
      : imp.proposals;
    return [...filtered].sort((a, b) => {
      const ea = EFFORT_RANK[normalizeEffort(a.effort)] ?? 1;
      const eb = EFFORT_RANK[normalizeEffort(b.effort)] ?? 1;
      if (ea !== eb) return ea - eb;
      const liftA = a.expectedLift ?? 0;
      const liftB = b.expectedLift ?? 0;
      return liftB - liftA;
    });
  }, [imp.proposals, effortFilter]);

  const lowCount = imp.proposals.filter((p) => normalizeEffort(p.effort) === "low").length;
  const acceptAllDisabled = lowCount === 0;

  return (
    <div className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">{imp.summary}</h3>
        <span className="eo-card-sub">
          trace{" "}
          <Link
            href={`/workspace/tracing/detail/?id=${encodeURIComponent(imp.traceId)}`}
            className="eo-link mono"
          >
            {imp.traceId.slice(0, 12)}
          </Link>{" "}
          · {fmtRel(imp.createdAt)}
        </span>
      </div>
      <div className="eo-page-meta" style={{ marginBottom: 6 }}>
        <span className="eo-tag">
          {t("quality.improvements.packId")}: {packLabel}
        </span>
        {imp.improvementContentLocale && (
          <span className="eo-tag">
            {t("quality.improvements.contentLocale")}: {imp.improvementContentLocale}
          </span>
        )}
        <span className="eo-tag">policy: {imp.consensusPolicy}</span>
        <span className="eo-tag">
          agreement: {(imp.agreementRatio * 100).toFixed(0)}%
        </span>
        <span className="eo-tag eo-tag-accent">
          judge cost: {fmtPrice(imp.judgeCostUsd)}
        </span>
        <span className="eo-tag">{imp.judgeModels.length} judges</span>
        <span className="eo-tag">status: {imp.status}</span>
      </div>
      <div style={{ display: "grid", gap: 6 }}>
        {sortedProposals.length === 0 && (
          <div className="eo-empty">{t("quality.improvements.noProposals")}</div>
        )}
        {sortedProposals.map((p, i) => (
          <ProposalRow key={i} p={p} uiLocale={uiLocale} />
        ))}
      </div>
      {writable && (
        <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
          <button
            type="button"
            className="eo-btn eo-btn-ghost"
            disabled={acceptAllDisabled}
            title={
              acceptAllDisabled
                ? t("quality.improvements.acceptAllDisabledHint")
                : t("quality.improvements.acceptAllHint")
            }
            onClick={() => onSetStatus("accepted")}
          >
            {t("quality.improvements.acceptAllLow")} ({lowCount})
          </button>
          {STATUS_OPTIONS.filter((s) => s !== imp.status && s !== "accepted").map((s) => (
            <button
              key={s}
              type="button"
              className="eo-btn eo-btn-ghost"
              onClick={() => onSetStatus(s)}
            >
              Mark {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ProposalRow({ p, uiLocale }: { p: ImprovementProposal; uiLocale: AppLocale }) {
  const { t } = useI18n();
  const eff = normalizeEffort(p.effort);
  const summary =
    uiLocale === "ko"
      ? p.categoryDetailSummaryI18n?.ko
      : p.categoryDetailSummaryI18n?.en;
  return (
    <div
      style={{
        padding: 8,
        border: "1px solid var(--eo-border)",
        borderRadius: 6,
        background: "var(--eo-bg-2)",
      }}
      data-effort={eff}
    >
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <span className="eo-tag eo-tag-accent">{categoryLabel(p, uiLocale)}</span>
        <strong>{proposalTitle(p, uiLocale)}</strong>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 6, alignItems: "center" }}>
          <span className="eo-mute" style={{ fontSize: 11 }}>
            lift +{(p.expectedLift * 100).toFixed(0)}%
          </span>
          <span
            className="eo-effort"
            data-effort={eff}
            title={p.effortReason || t(`quality.improvements.effortHint.${eff}`)}
          >
            {t(`quality.improvements.effort.${eff}`)}
          </span>
        </span>
      </div>
      {summary && (
        <div className="eo-mute" style={{ fontSize: 11, marginTop: 4 }}>
          {summary}
        </div>
      )}
      <div style={{ fontSize: 12, marginTop: 4 }}>{p.rationale}</div>
      {p.secondaryCandidates && p.secondaryCandidates.length > 0 && (
        <div className="eo-improvement-secondary">
          <span style={{ fontWeight: 600 }}>
            {t("quality.improvements.secondaryAlsoTry")}:
          </span>
          {p.secondaryCandidates.map((s) => {
            const seff = normalizeEffort(s.effort);
            return (
              <span key={s.category} title={`effort: ${seff}`}>
                {secondaryLabel(s, uiLocale)} <em data-effort={seff}>·{seff}</em>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
