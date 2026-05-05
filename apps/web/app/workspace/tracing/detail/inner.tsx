"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { fetchTraceDetail, type SpanRow } from "@/lib/api";
import { SpanLlmPanel, TraceLlmSummaryCard } from "@/components/llm-view";
import { fmtMs, fmtTime, truncate } from "@/lib/format";
import { useI18n } from "@/lib/i18n/context";

export type TreeNode = SpanRow & { depth: number; children: TreeNode[] };

export function buildTree(spans: SpanRow[]): { flat: TreeNode[]; roots: TreeNode[] } {
  const byId = new Map<string, TreeNode>();
  for (const s of spans) {
    byId.set(s.spanId, { ...s, depth: 0, children: [] });
  }
  const roots: TreeNode[] = [];
  for (const node of byId.values()) {
    const pid = node.parentSpanId;
    const isRoot = !pid || pid === "" || pid === "0000000000000000" || !byId.has(pid);
    if (isRoot) roots.push(node);
    else byId.get(pid!)!.children.push(node);
  }
  const walk = (nodes: TreeNode[], d: number, acc: TreeNode[]) => {
    nodes.sort((a, b) => Number(a.startTimeUnixNano ?? 0) - Number(b.startTimeUnixNano ?? 0));
    for (const n of nodes) {
      n.depth = d;
      acc.push(n);
      walk(n.children, d + 1, acc);
    }
  };
  const flat: TreeNode[] = [];
  walk(roots, 0, flat);
  return { flat, roots };
}

function statusTone(s: string): "ok" | "err" | "" {
  if (s === "ERROR") return "err";
  if (s === "OK") return "ok";
  return "";
}

export function TraceDetailInner() {
  const { t, tsub } = useI18n();
  const params = useSearchParams();
  const id = params.get("id") || "";
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<"llm" | "attributes" | "events" | "raw">("llm");
  const [zoom, setZoom] = useState(1);
  const [showFlowModal, setShowFlowModal] = useState(false);

  const q = useQuery({
    queryKey: ["trace", id],
    queryFn: () => fetchTraceDetail(id),
    enabled: Boolean(id),
  });

  const tree = useMemo(() => buildTree(q.data?.spans ?? []), [q.data?.spans]);
  const flatSpans = tree.flat;
  const treeRoots = tree.roots;

  const active: TreeNode | null = useMemo(() => {
    if (!flatSpans.length) return null;
    return (activeId && flatSpans.find((s) => s.spanId === activeId)) || flatSpans[0];
  }, [flatSpans, activeId]);

  const { minStart, maxEnd } = useMemo(() => {
    const starts = flatSpans
      .map((s) => Number(s.startTimeUnixNano ?? 0))
      .filter((n) => n > 0);
    const ends = flatSpans
      .map((s) => Number(s.endTimeUnixNano ?? 0))
      .filter((n) => n > 0);
    return {
      minStart: starts.length ? Math.min(...starts) : 0,
      maxEnd: ends.length ? Math.max(...ends) : 0,
    };
  }, [flatSpans]);

  const totalDurationNs = Math.max(1, maxEnd - minStart);
  const totalMs = totalDurationNs / 1e6;

  if (!id) {
    return (
      <div className="eo-empty">
        {t("pages.traceDetail.missingId")}{" "}
        <Link href="/workspace/tracing/">{t("pages.traceDetail.backLink")}</Link>
      </div>
    );
  }

  return (
    <>
      <div className="eo-page-head">
        <div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <Link href="/workspace/tracing/" className="eo-btn">
              {t("pages.traceDetail.backLink")}
            </Link>
            <h1 className="eo-page-title" style={{ marginLeft: 4 }}>
              {q.data?.rootName || t("pages.traceDetail.titleFallback")}
            </h1>
            <span
              className="eo-status"
              data-tone={statusTone(q.data?.status || "")}
              style={{ marginLeft: 8 }}
            >
              {q.data?.status || "—"}
            </span>
          </div>
          <p className="eo-page-lede">{t("pages.traceDetail.lede")}</p>
        </div>
        <div className="eo-page-meta">
          <span className="eo-tag mono">{truncate(q.data?.traceId || id, 18)}</span>
          <span className="eo-tag">{q.data?.serviceName || "—"}</span>
          <span className="eo-tag eo-tag-accent">{fmtMs(totalMs)}</span>
          <Link
            href={`/workspace/quality/runs/?prefill-trace=${encodeURIComponent(id)}`}
            className="eo-btn eo-btn-primary"
            style={{ fontSize: 12 }}
            title="Start an evaluation Run scoped to this trace"
          >
            Evaluate ▶
          </Link>
          <Link
            href={`/workspace/quality/golden/?tab=labels&traceId=${encodeURIComponent(id)}`}
            className="eo-btn"
            style={{ fontSize: 12 }}
            title="Open the Human Label form pre-filled with this trace id"
          >
            + Human label
          </Link>
          <div className="eo-seg">
            {[1, 2, 4].map((z) => (
              <button
                key={z}
                type="button"
                data-active={zoom === z}
                onClick={() => setZoom(z)}
              >
                ×{z}
              </button>
            ))}
          </div>
        </div>
      </div>

      {q.isLoading && <div className="eo-empty">{t("pages.traceDetail.loading")}</div>}
      {q.isError && <div className="eo-empty">{t("pages.traceDetail.loadError")}</div>}

      {q.data?.llmSummary && (
        <section className="eo-card" style={{ padding: 12, marginBottom: 12 }}>
          <div className="eo-card-h">
            <h3 className="eo-card-title">{t("pages.traceDetail.llmSummary")}</h3>
            <span className="eo-card-sub">
              {tsub("pages.traceDetail.aggregatedFrom", {
                n: String(q.data.spans?.length ?? 0),
              })}
            </span>
          </div>
          <TraceLlmSummaryCard summary={q.data.llmSummary} />
        </section>
      )}

      {q.data && (
        <div
          className="eo-trace-grid"
          style={{
            display: "grid",
            gap: 12,
            gridTemplateColumns:
              "minmax(190px, 14%) minmax(280px, 27%) minmax(320px, 34%) minmax(280px, 25%)",
          }}
        >
          <section className="eo-card" style={{ padding: 10 }}>
            <div className="eo-card-h">
              <h3 className="eo-card-title">{t("pages.traceDetail.spanTree")}</h3>
              <span className="eo-card-sub">
                {tsub("pages.traceDetail.spansCount", { n: String(flatSpans.length) })}
              </span>
            </div>
            <div className="eo-span-tree">
              {flatSpans.map((s) => {
                const ms =
                  s.startTimeUnixNano && s.endTimeUnixNano
                    ? (Number(s.endTimeUnixNano) - Number(s.startTimeUnixNano)) / 1e6
                    : 0;
                const kindLabel =
                  s.llm?.kind?.toUpperCase() ||
                  (statusTone(s.status) === "err" ? "ERR" : "SPN");
                return (
                  <div
                    key={s.spanId}
                    className="eo-span-row"
                    data-active={active?.spanId === s.spanId}
                    onClick={() => setActiveId(s.spanId)}
                    title={s.name}
                  >
                    <span className="eo-span-name">
                      <span
                        className="eo-span-indent"
                        style={{ width: s.depth * 12 }}
                      >
                        {s.depth > 0 ? "└" : ""}
                      </span>
                      <span className="eo-span-kind" data-kind={s.llm?.kind ?? ""}>
                        {kindLabel.slice(0, 3)}
                      </span>
                      {truncate(s.name, 24)}
                    </span>
                    <span className="eo-span-dur">{fmtMs(ms)}</span>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="eo-card" style={{ padding: 10 }}>
            <div className="eo-card-h">
              <h3 className="eo-card-title">{t("pages.traceDetail.flow")}</h3>
              <span className="eo-card-sub">
                {tsub("pages.traceDetail.flowSub", { roots: String(treeRoots.length) })}
              </span>
              <button
                type="button"
                className="eo-btn eo-btn-flow"
                style={{ marginLeft: "auto", fontSize: 11, padding: "3px 8px" }}
                onClick={() => setShowFlowModal(true)}
                title="View flow in full screen"
              >
                ⬡ Expand
              </button>
            </div>
            <SpanFlowGraph
              roots={treeRoots}
              activeId={active?.spanId ?? null}
              onSelect={setActiveId}
            />
          </section>

          <section className="eo-card" style={{ padding: 12 }}>
            <div className="eo-card-h">
              <h3 className="eo-card-title">{t("pages.traceDetail.timeline")}</h3>
              <span className="eo-card-sub">
                {tsub("pages.traceDetail.timelineSub", {
                  total: fmtMs(totalMs),
                  zoom: String(zoom),
                })}
              </span>
            </div>
            <div className="eo-timeline">
              <div className="eo-timeline-axis">
                <span>{t("pages.traceDetail.msAxis0")}</span>
                <span>{fmtMs(totalMs / 2)}</span>
                <span>{fmtMs(totalMs)}</span>
              </div>
              {flatSpans.map((s) => {
                const startNs = Number(s.startTimeUnixNano ?? 0);
                const endNs = Number(s.endTimeUnixNano ?? 0);
                const offset = Math.max(0, (startNs - minStart) / totalDurationNs) * 100 * zoom;
                const width =
                  Math.max(0.3, ((endNs - startNs) / totalDurationNs) * 100) * zoom;
                return (
                  <div
                    key={s.spanId}
                    className="eo-timeline-row"
                    onClick={() => setActiveId(s.spanId)}
                    style={{ cursor: "pointer" }}
                  >
                    <span className="eo-timeline-label">
                      <span
                        className="eo-span-indent"
                        style={{ display: "inline-block", width: s.depth * 10 }}
                      />
                      {truncate(s.name, 22)}
                    </span>
                    <div className="eo-timeline-track">
                      <span
                        className="eo-timeline-fill"
                        data-tone={statusTone(s.status)}
                        style={{
                          left: `${offset}%`,
                          width: `${Math.min(100 * zoom - offset, width)}%`,
                        }}
                      />
                    </div>
                    <span className="eo-timeline-dur">
                      {fmtMs((endNs - startNs) / 1e6)}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="eo-card" style={{ padding: 10 }}>
            <div className="eo-card-h">
              <h3 className="eo-card-title">{active?.name || t("pages.traceDetail.spanFallback")}</h3>
              <span className="eo-card-sub">
                {active ? fmtMs(
                  active.startTimeUnixNano && active.endTimeUnixNano
                    ? (Number(active.endTimeUnixNano) - Number(active.startTimeUnixNano)) / 1e6
                    : 0,
                ) : "—"}
              </span>
            </div>
            <div className="eo-drawer-tabs" style={{ borderTop: 0, marginBottom: 8 }}>
              <button
                type="button"
                data-active={detailTab === "llm"}
                onClick={() => setDetailTab("llm")}
              >
                {t("pages.traceDetail.tabLlm")}
              </button>
              <button
                type="button"
                data-active={detailTab === "attributes"}
                onClick={() => setDetailTab("attributes")}
              >
                {t("pages.traceDetail.tabAttributes")}
              </button>
              <button
                type="button"
                data-active={detailTab === "events"}
                onClick={() => setDetailTab("events")}
              >
                {tsub("pages.traceDetail.eventsCount", {
                  n: String(active?.events?.length ?? 0),
                })}
              </button>
              <button
                type="button"
                data-active={detailTab === "raw"}
                onClick={() => setDetailTab("raw")}
              >
                {t("pages.traceDetail.tabRaw")}
              </button>
            </div>

            {active && detailTab === "llm" && (
              <SpanLlmPanel llm={active.llm ?? null} />
            )}
            {active && detailTab === "attributes" && (
              <AttributesView attributes={active.attributes ?? []} />
            )}
            {active && detailTab === "events" && (
              <EventsView events={active.events ?? []} />
            )}
            {active && detailTab === "raw" && (
              <pre className="eo-code">{JSON.stringify(active, null, 2)}</pre>
            )}

            <div className="eo-divider" style={{ margin: "12px 0" }} />
            <div className="eo-card-sub" style={{ marginBottom: 4 }}>{t("pages.traceDetail.identity")}</div>
            <dl className="eo-kv-list">
              <div className="eo-kv-row">
                <dt>{t("pages.traceDetail.spanId")}</dt>
                <dd>{active?.spanId}</dd>
              </div>
              <div className="eo-kv-row">
                <dt>{t("pages.traceDetail.parentId")}</dt>
                <dd>{active?.parentSpanId || t("pages.traceDetail.root")}</dd>
              </div>
              <div className="eo-kv-row">
                <dt>{t("pages.traceDetail.started")}</dt>
                <dd>
                  {active?.startTimeUnixNano
                    ? fmtTime(new Date(Number(active.startTimeUnixNano) / 1e6).toISOString())
                    : "—"}
                </dd>
              </div>
            </dl>
          </section>
        </div>
      )}

      {showFlowModal && q.data && (
        <div
          className="eo-modal-backdrop"
          onClick={() => setShowFlowModal(false)}
        >
          <div
            className="eo-modal"
            style={{ maxWidth: "92vw", maxHeight: "88vh" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="eo-modal-h">
              <div>
                <h2 className="eo-modal-title">Span Flow Graph</h2>
                <p className="eo-modal-sub">
                  {q.data.rootName || "Trace"} — {flatSpans.length} spans, {treeRoots.length} root(s)
                </p>
              </div>
              <button
                type="button"
                className="eo-modal-x"
                onClick={() => setShowFlowModal(false)}
              >
                ×
              </button>
            </div>
            <div className="eo-modal-body" style={{ overflow: "auto", padding: 20 }}>
              <SpanFlowGraph
                roots={treeRoots}
                activeId={active?.spanId ?? null}
                onSelect={setActiveId}
              />
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function AttributesView({
  attributes,
}: {
  attributes: Array<{ key: string; value?: Record<string, unknown> }>;
}) {
  const { t } = useI18n();
  if (!attributes || attributes.length === 0) {
    return <div className="eo-empty">{t("pages.traceDetail.noAttributes")}</div>;
  }
  return (
    <dl className="eo-kv-list">
      {attributes.map((a) => {
        const v = a.value ?? {};
        const shown = Object.values(v)[0];
        return (
          <div className="eo-kv-row" key={a.key}>
            <dt>{a.key}</dt>
            <dd>{String(shown ?? "")}</dd>
          </div>
        );
      })}
    </dl>
  );
}

function EventsView({
  events,
}: {
  events: Array<{ name: string; timeUnixNano?: number; attributes?: unknown }>;
}) {
  const { t } = useI18n();
  if (!events || events.length === 0)
    return <div className="eo-empty">{t("pages.traceDetail.noEvents")}</div>;
  return (
    <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
      {events.map((e, i) => (
        <li key={i} className="eo-card" style={{ padding: 10, boxShadow: "none" }}>
          <div style={{ fontWeight: 600 }}>{e.name}</div>
          {e.timeUnixNano && (
            <div className="eo-td-sub">
              {fmtTime(new Date(Number(e.timeUnixNano) / 1e6).toISOString())}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Flow graph — horizontal tree diagram with SVG edges + clickable node boxes.
// ---------------------------------------------------------------------------

type FlowLayoutNode = {
  span: TreeNode;
  x: number;
  y: number;
  w: number;
  h: number;
  children: FlowLayoutNode[];
};

type FlowLayout = {
  nodes: FlowLayoutNode[];
  edges: Array<{ parent: FlowLayoutNode; child: FlowLayoutNode }>;
  width: number;
  height: number;
};

const FLOW_NODE_W = 148;
const FLOW_NODE_H = 40;
const FLOW_COL_GAP = 16;
const FLOW_ROW_GAP = 30;

/** Post-order layout: leaves get stacked columns, parents centre over children.
 * Flow is top → down: root sits at the top of the canvas, children fan out
 * horizontally below, grandchildren below them, etc. */
function layoutFlow(roots: TreeNode[]): FlowLayout {
  let cursor = 0;
  const colStride = FLOW_NODE_W + FLOW_COL_GAP;
  const rowStride = FLOW_NODE_H + FLOW_ROW_GAP;

  const visit = (spans: TreeNode[], depth: number): FlowLayoutNode[] =>
    spans.map((span) => {
      const node: FlowLayoutNode = {
        span,
        x: 0,
        y: depth * rowStride,
        w: FLOW_NODE_W,
        h: FLOW_NODE_H,
        children: visit(span.children, depth + 1),
      };
      if (node.children.length === 0) {
        node.x = cursor * colStride;
        cursor += 1;
      } else {
        const first = node.children[0];
        const last = node.children[node.children.length - 1];
        node.x = (first.x + last.x) / 2;
      }
      return node;
    });

  const layoutRoots = visit(roots, 0);
  const all: FlowLayoutNode[] = [];
  const collect = (ns: FlowLayoutNode[]) => {
    for (const n of ns) {
      all.push(n);
      collect(n.children);
    }
  };
  collect(layoutRoots);

  const edges: FlowLayout["edges"] = [];
  const connect = (n: FlowLayoutNode) => {
    for (const c of n.children) {
      edges.push({ parent: n, child: c });
      connect(c);
    }
  };
  layoutRoots.forEach(connect);

  const width = all.reduce((m, n) => Math.max(m, n.x + n.w), 0);
  const height = all.reduce((m, n) => Math.max(m, n.y + n.h), 0);
  return { nodes: all, edges, width, height };
}

function durationMs(s: TreeNode): number {
  if (!s.startTimeUnixNano || !s.endTimeUnixNano) return 0;
  return (Number(s.endTimeUnixNano) - Number(s.startTimeUnixNano)) / 1e6;
}

function flowKindLabel(s: TreeNode): string {
  const k = s.llm?.kind;
  if (k) return k.toUpperCase();
  if (statusTone(s.status) === "err") return "ERR";
  return "SPAN";
}

export function SpanFlowGraph({
  roots,
  activeId,
  onSelect,
}: {
  roots: TreeNode[];
  activeId: string | null;
  onSelect: (id: string) => void;
}) {
  const layout = useMemo(() => layoutFlow(roots), [roots]);
  if (!layout.nodes.length) {
    return <div className="eo-empty">No spans</div>;
  }

  // Pad 1 px so strokes at the edge don't get clipped.
  const padding = 6;
  const vbW = layout.width + padding * 2;
  const vbH = layout.height + padding * 2;

  return (
    <div className="eo-flow-scroll">
      <svg
        className="eo-flow-svg"
        viewBox={`${-padding} ${-padding} ${vbW} ${vbH}`}
        width={vbW}
        height={vbH}
        role="img"
        aria-label="Span flow graph"
      >
        <g className="eo-flow-edges">
          {layout.edges.map(({ parent, child }) => {
            const x1 = parent.x + parent.w / 2;
            const y1 = parent.y + parent.h;
            const x2 = child.x + child.w / 2;
            const y2 = child.y;
            const cy = (y1 + y2) / 2;
            return (
              <path
                key={`${parent.span.spanId}->${child.span.spanId}`}
                d={`M ${x1} ${y1} C ${x1} ${cy}, ${x2} ${cy}, ${x2} ${y2}`}
                data-active={
                  activeId === parent.span.spanId ||
                  activeId === child.span.spanId
                }
              />
            );
          })}
        </g>
        <g className="eo-flow-nodes">
          {layout.nodes.map((n) => {
            const active = activeId === n.span.spanId;
            return (
              <g
                key={n.span.spanId}
                className="eo-flow-node"
                transform={`translate(${n.x}, ${n.y})`}
                data-active={active}
                data-tone={statusTone(n.span.status) || undefined}
                data-kind={n.span.llm?.kind ?? ""}
                onClick={() => onSelect(n.span.spanId)}
              >
                <rect
                  className="eo-flow-node-bg"
                  x={0}
                  y={0}
                  rx={7}
                  ry={7}
                  width={n.w}
                  height={n.h}
                />
                <rect
                  className="eo-flow-node-stripe"
                  x={0}
                  y={0}
                  rx={7}
                  ry={7}
                  width={n.w}
                  height={4}
                />
                <text className="eo-flow-node-name" x={10} y={20}>
                  {truncate(n.span.name, 20)}
                </text>
                <text className="eo-flow-node-meta" x={10} y={33}>
                  {flowKindLabel(n.span)} · {fmtMs(durationMs(n.span))}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}
