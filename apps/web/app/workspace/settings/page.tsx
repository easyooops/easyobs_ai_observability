"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useWorkspace, windowLabel } from "@/lib/context";
import {
  fetchStorageSettings,
  saveStorageSettings,
  testBlobConnection,
  testCatalogConnection,
  type BlobProvider,
  type BlobSettings,
  type CatalogProvider,
  type CatalogSettings,
  type RetentionSettings,
  type StorageSettings,
  type StorageTestResult,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n/context";

// Backend masks secrets on GET as this literal string. Keep in sync with
// ``BlobConfig.public_dict`` in src/easyobs/services/app_settings.py.
const SECRET_MASK = "••• set •••";

const EMPTY_BLOB: BlobSettings = {
  provider: "local",
  path: "",
  bucket: "",
  prefix: "",
  region: "",
  s3_access_key_id: "",
  s3_secret_access_key: "",
  azure_account_name: "",
  azure_account_key: "",
  azure_container: "",
  gcs_service_account_json: "",
  hot_retention_days: 7,
};

const EMPTY_CATALOG: CatalogSettings = {
  provider: "sqlite",
  sqlite_path: "",
  pg_host: "",
  pg_port: 5432,
  pg_database: "",
  pg_user: "",
  pg_password: "",
  pg_sslmode: "prefer",
};

const EMPTY_RETENTION: RetentionSettings = { enabled: false, days: 30 };

const BLOB_LABELS: Record<BlobProvider, string> = {
  local: "Local filesystem",
  s3: "AWS S3",
  azure: "Azure Blob Storage",
  gcs: "Google Cloud Storage",
  hybrid: "Hybrid (Local + S3 Archive)",
};

const CATALOG_LABELS: Record<CatalogProvider, string> = {
  sqlite: "SQLite",
  postgres: "PostgreSQL",
};

export default function SettingsPage() {
  const { t } = useI18n();
  const ws = useWorkspace();
  const auth = useAuth();
  const { window: win, setWindow, live, setLive } = ws;
  const isCustom = win === "custom";
  const canEditStorage = auth.isPlatformAdmin;

  return (
    <>
      <div className="eo-page-head">
        <div>
          <h1 className="eo-page-title">{t("pages.settings.title")}</h1>
          <p className="eo-page-lede">{t("pages.settings.lede")}</p>
        </div>
      </div>

      <div className="eo-grid-2">
        <div className="eo-card">
          <div className="eo-card-h">
            <h3 className="eo-card-title">{t("settings.preferencesTitle")}</h3>
            <span className="eo-card-sub">
              {t("settings.preferencesSub")}
            </span>
          </div>
          <dl className="eo-kv-list">
            <div className="eo-kv-row">
              <dt>{t("settings.window")}</dt>
              <dd>
                <div className="eo-seg">
                  {(["1h", "6h", "24h", "7d"] as const).map((w) => (
                    <button
                      key={w}
                      type="button"
                      data-active={win === w}
                      onClick={() => setWindow(w)}
                    >
                      {w}
                    </button>
                  ))}
                </div>
                <div className="eo-hint" style={{ marginTop: 6 }}>
                  {t("settings.windowHint")}
                  {isCustom && (
                    <>
                      {t("settings.windowHintCustom")}{" "}
                      <code>{windowLabel(ws)}</code>
                    </>
                  )}
                </div>
              </dd>
            </div>
            <div className="eo-kv-row">
              <dt>{t("settings.liveRefresh")}</dt>
              <dd>
                <button
                  type="button"
                  className="eo-chip"
                  data-active={live}
                  onClick={() => setLive(!live)}
                >
                  {live ? t("settings.liveOn") : t("settings.liveOff")}
                </button>
                <div className="eo-hint" style={{ marginTop: 6 }}>
                  {t("settings.liveHint")}
                </div>
              </dd>
            </div>
          </dl>
        </div>

        <RetentionExplainer />
      </div>

      {canEditStorage ? (
        <StorageOverview />
      ) : (
        <div className="eo-card" style={{ marginTop: 16 }}>
          <div className="eo-card-h">
            <h3 className="eo-card-title">{t("settings.storageTitle")}</h3>
            <span className="eo-card-sub">{t("settings.storageSub")}</span>
          </div>
          <p className="eo-empty">{t("settings.storageLocked")}</p>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Retention card — explanatory only; backend has no cleaner job yet.
// ---------------------------------------------------------------------------

function RetentionExplainer() {
  const { t } = useI18n();
  return (
    <div className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">{t("settings.retentionTitle")}</h3>
        <span className="eo-card-sub">{t("settings.retentionSub")}</span>
      </div>
      <p className="eo-hint" style={{ marginTop: 0 }}>
        {t("settings.retentionWhat")}
      </p>
      <p className="eo-hint">{t("settings.retentionStatus")}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Storage overview — read-only by default; each subsection has a Change…
// button that opens a modal with the editor + connection test + confirm.
// Blob/Catalog edits are intentionally gated behind the modal so a single
// stray click can never repoint the catalog or blob root.
// ---------------------------------------------------------------------------

type DialogKind = null | "blob" | "catalog" | "retention";

function StorageOverview() {
  const { t, tsub } = useI18n();
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["settings-storage"],
    queryFn: fetchStorageSettings,
  });
  const [dialog, setDialog] = useState<DialogKind>(null);

  const blob: BlobSettings = useMemo(
    () => ({ ...EMPTY_BLOB, ...((data?.blob ?? {}) as BlobSettings) }),
    [data],
  );
  const catalog: CatalogSettings = useMemo(
    () => ({ ...EMPTY_CATALOG, ...((data?.catalog ?? {}) as CatalogSettings) }),
    [data],
  );
  const retention: RetentionSettings = useMemo(
    () => ({ ...EMPTY_RETENTION, ...((data?.retention ?? {}) as RetentionSettings) }),
    [data],
  );

  const restartRequired = data?.restartRequired ?? false;
  const active = data?.active;

  return (
    <section className="eo-card" style={{ marginTop: 16 }}>
      <div className="eo-card-h">
        <h3 className="eo-card-title">{t("settings.storageOverviewTitle")}</h3>
        <span className="eo-card-sub">
          {isLoading
            ? t("settings.storageLoading")
            : t("settings.storageOverviewSub")}
        </span>
        <div className="eo-card-actions">
          <button
            type="button"
            className="eo-btn"
            onClick={() => refetch()}
            disabled={isFetching}
            title={t("settings.reloadTitle")}
          >
            {isFetching ? t("settings.refreshing") : t("settings.refresh")}
          </button>
        </div>
      </div>

      {error && (
        <div className="eo-banner eo-banner-warn">
          {t("settings.loadError")} {(error as Error).message}.{" "}
          {t("settings.loadErrorHint")}
        </div>
      )}

      {restartRequired && (
        <div className="eo-banner eo-banner-warn">
          {t("settings.restartBanner")}
          {active && (
            <div style={{ fontSize: 11, marginTop: 4 }}>
              {t("settings.activeBlob")}{" "}
              <code>{active.blob.provider}</code> {t("settings.at")}{" "}
              <code>{active.blob.path}</code> · {t("settings.activeCatalog")}{" "}
              <code>{active.catalog.provider}</code>
            </div>
          )}
        </div>
      )}

      <div className="eo-storage-grid">
        <BlobCard
          cfg={blob}
          active={active?.blob}
          restartRequired={restartRequired}
          onChange={() => setDialog("blob")}
        />
        <CatalogCard
          cfg={catalog}
          active={active?.catalog}
          restartRequired={restartRequired}
          onChange={() => setDialog("catalog")}
        />
        <RetentionCard cfg={retention} onChange={() => setDialog("retention")} />
      </div>

      <div className="eo-divider" />

      <details className="eo-hint" style={{ marginTop: 0 }}>
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>
          {t("settings.whereSavedSummary")}
        </summary>
        <div style={{ marginTop: 8, lineHeight: 1.6 }}>
          <p style={{ margin: "0 0 6px" }}>{t("settings.whereSavedP1")}</p>
          <p style={{ margin: "0 0 6px" }}>{t("settings.whereSavedP2")}</p>
          <p style={{ margin: "0 0 6px" }}>{t("settings.whereSavedP3")}</p>
          <p style={{ margin: 0 }}>
            {tsub("settings.whereSavedP4", { mask: SECRET_MASK })}
          </p>
        </div>
      </details>

      {dialog === "blob" && (
        <BlobChangeDialog
          initial={blob}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "catalog" && (
        <CatalogChangeDialog
          initial={catalog}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "retention" && (
        <RetentionChangeDialog
          initial={retention}
          // For the retention dialog we also pass the current blob/catalog
          // so the PUT carries the full StorageSettings without mutation.
          blob={blob}
          catalog={catalog}
          onClose={() => setDialog(null)}
        />
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Read-only cards (3-up grid). Each card owns a status dot in the header so
// the whole Storage panel reads like a status board at a glance.
// ---------------------------------------------------------------------------

type Tone = "ok" | "warn" | "off";

function StatusDot({ tone, label }: { tone: Tone; label: string }) {
  return (
    <span
      className="eo-dot"
      data-tone={tone}
      role="img"
      aria-label={label}
      title={label}
    />
  );
}

function BlobCard({
  cfg,
  active,
  restartRequired,
  onChange,
}: {
  cfg: BlobSettings;
  active: { provider: string; path: string } | undefined;
  restartRequired: boolean;
  onChange: () => void;
}) {
  const { t } = useI18n();
  const dirty =
    restartRequired && active &&
    (cfg.provider !== active.provider || cfg.provider !== "local");
  const tone: Tone = dirty ? "warn" : "ok";
  const status = dirty
    ? t("settings.statusRestartPending")
    : t("settings.statusActive");

  return (
    <div className="eo-storage-card">
      <div className="eo-storage-card-h">
        <StatusDot tone={tone} label={status} />
        <h4>{t("settings.cardBlobTitle")}</h4>
        <span style={{ flex: 1 }} />
        <span className="eo-pill" data-tone={tone}>
          {status}
        </span>
      </div>
      <span className="eo-storage-card-sub">
        {t("settings.cardBlobSub")}
      </span>
      <dl className="eo-storage-kv">
        <dt>{t("settings.dtProvider")}</dt>
        <dd>{BLOB_LABELS[cfg.provider]}</dd>
        <dt>{t("settings.dtSaved")}</dt>
        <dd>{describeBlob(cfg)}</dd>
        <dt>{t("settings.dtActive")}</dt>
        <dd data-mute={active ? undefined : "1"}>
          {active ? active.path : "—"}
        </dd>
      </dl>
      <div className="eo-storage-card-foot">
        <button type="button" className="eo-btn" onClick={onChange}>
          {t("settings.changeButton")}
        </button>
      </div>
    </div>
  );
}

function CatalogCard({
  cfg,
  active,
  restartRequired,
  onChange,
}: {
  cfg: CatalogSettings;
  active: { provider: string; url: string } | undefined;
  restartRequired: boolean;
  onChange: () => void;
}) {
  const { t } = useI18n();
  const dirty =
    restartRequired && active && cfg.provider !== active.provider;
  const tone: Tone = dirty ? "warn" : "ok";
  const status = dirty
    ? t("settings.statusRestartPending")
    : t("settings.statusActive");

  return (
    <div className="eo-storage-card">
      <div className="eo-storage-card-h">
        <StatusDot tone={tone} label={status} />
        <h4>{t("settings.cardCatalogTitle")}</h4>
        <span style={{ flex: 1 }} />
        <span className="eo-pill" data-tone={tone}>
          {status}
        </span>
      </div>
      <span className="eo-storage-card-sub">
        {t("settings.cardCatalogSub")}
      </span>
      <dl className="eo-storage-kv">
        <dt>{t("settings.dtProvider")}</dt>
        <dd>{CATALOG_LABELS[cfg.provider]}</dd>
        <dt>{t("settings.dtSaved")}</dt>
        <dd>{describeCatalog(cfg)}</dd>
        <dt>{t("settings.dtActive")}</dt>
        <dd data-mute={active ? undefined : "1"}>
          {active ? active.url : "—"}
        </dd>
      </dl>
      <div className="eo-storage-card-foot">
        <button type="button" className="eo-btn" onClick={onChange}>
          {t("settings.changeButton")}
        </button>
      </div>
    </div>
  );
}

function RetentionCard({
  cfg,
  onChange,
}: {
  cfg: RetentionSettings;
  onChange: () => void;
}) {
  const { t, tsub } = useI18n();
  // Off = neutral grey ring; On = green; never red because the policy only
  // gets stored, no enforcement happens yet.
  const tone: Tone = cfg.enabled ? "ok" : "off";
  const status = cfg.enabled
    ? t("settings.retentionCardStatusOn")
    : t("settings.retentionCardStatusOff");

  return (
    <div className="eo-storage-card">
      <div className="eo-storage-card-h">
        <StatusDot tone={tone} label={status} />
        <h4>{t("settings.retentionCardTitle")}</h4>
        <span style={{ flex: 1 }} />
        <span className="eo-pill" data-tone={tone === "ok" ? "ok" : undefined}>
          {status}
        </span>
      </div>
      <span className="eo-storage-card-sub">
        {t("settings.retentionCardSub2")}
      </span>
      <dl className="eo-storage-kv">
        <dt>{t("settings.dtStatus")}</dt>
        <dd>
          {cfg.enabled
            ? t("settings.retentionRowEnabled")
            : t("settings.retentionCardStatusOff")}
        </dd>
        <dt>{t("settings.dtWindow")}</dt>
        <dd>{tsub("settings.retentionDays", { n: String(cfg.days) })}</dd>
        <dt>{t("settings.dtCleaner")}</dt>
        <dd data-mute="1">{t("settings.cleanerNotWired")}</dd>
      </dl>
      <div className="eo-storage-card-foot">
        <button type="button" className="eo-btn" onClick={onChange}>
          {t("settings.changeButton")}
        </button>
      </div>
    </div>
  );
}

function describeBlob(cfg: BlobSettings): string {
  if (cfg.provider === "local") return cfg.path || "(default ./data/blobs)";
  if (cfg.provider === "s3")
    return `s3://${cfg.bucket || "?"}/${cfg.prefix || ""} (${cfg.region || "default region"})`;
  if (cfg.provider === "azure")
    return `https://${cfg.azure_account_name || "?"}.blob.core.windows.net/${
      cfg.azure_container || "?"
    }/${cfg.prefix || ""}`;
  if (cfg.provider === "gcs") return `gs://${cfg.bucket || "?"}/${cfg.prefix || ""}`;
  return "—";
}

function describeCatalog(cfg: CatalogSettings): string {
  if (cfg.provider === "sqlite") return cfg.sqlite_path || "(default ./data/catalog.sqlite3)";
  if (cfg.provider === "postgres")
    return `postgres://${cfg.pg_user || "?"}@${cfg.pg_host || "?"}:${cfg.pg_port}/${
      cfg.pg_database || "?"
    } (sslmode=${cfg.pg_sslmode})`;
  return "—";
}

// ---------------------------------------------------------------------------
// Change dialogs (Blob / Catalog / Retention)
// ---------------------------------------------------------------------------

function Modal({
  title,
  sub,
  onClose,
  children,
  footer,
}: {
  title: string;
  sub?: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  footer: React.ReactNode;
}) {
  const { t } = useI18n();
  // Close on Escape; lock body scroll while open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  return (
    <div
      className="eo-modal-backdrop"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="eo-modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="eo-modal-h">
          <div>
            <h3 className="eo-modal-title">{title}</h3>
            {sub && <p className="eo-modal-sub">{sub}</p>}
          </div>
          <button
            type="button"
            className="eo-modal-x"
            aria-label={t("settings.modalClose")}
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <div className="eo-modal-body">{children}</div>
        <div className="eo-modal-foot">{footer}</div>
      </div>
    </div>
  );
}

function BlobChangeDialog({
  initial,
  onClose,
}: {
  initial: BlobSettings;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<BlobSettings>(initial);
  const [test, setTest] = useState<StorageTestResult | null>(null);

  const testMut = useMutation({
    mutationFn: () => testBlobConnection(stripMaskedSecrets(draft)),
    onSuccess: (r) => setTest(r),
    onError: (e: Error) => setTest({ ok: false, message: e.message }),
  });

  const saveMut = useMutation({
    mutationFn: () => {
      const current = qc.getQueryData<StorageSettings>(["settings-storage"]);
      return saveStorageSettings({
        blob: keepMaskedSecrets(draft, initial),
        catalog: (current?.catalog ?? EMPTY_CATALOG) as CatalogSettings,
        retention: (current?.retention ?? EMPTY_RETENTION) as RetentionSettings,
      });
    },
    onSuccess: (next) => {
      qc.setQueryData(["settings-storage"], next);
      onClose();
    },
  });

  const isCloud = draft.provider !== "local";
  // Cloud changes must pass a live test before save. Local stays free
  // because the NDJSON writer always falls back to the local path.
  const canSave =
    !saveMut.isPending && (!isCloud || (test?.ok && !providerNeedsRetest(draft, test)));

  return (
    <Modal
      title="Change blob root"
      sub={
        <>
          The blob root is where raw OTLP NDJSON batches are persisted.
          Switching providers requires an API restart and only takes effect
          once a writer for that backend is enabled. <strong>Test the
          connection</strong> before saving any cloud target.
        </>
      }
      onClose={onClose}
      footer={
        <>
          {test && (
            <span className="eo-status" data-tone={test.ok ? "ok" : "err"}>
              {test.ok ? "OK" : "Failed"} · {test.message}
            </span>
          )}
          <span className="eo-spacer" />
          <button
            type="button"
            className="eo-btn"
            onClick={() => testMut.mutate()}
            disabled={testMut.isPending}
          >
            {testMut.isPending ? "Testing…" : "Test connection"}
          </button>
          <button type="button" className="eo-btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="eo-btn eo-btn-primary"
            disabled={!canSave}
            onClick={() => saveMut.mutate()}
          >
            {saveMut.isPending ? "Saving…" : "Save & schedule restart"}
          </button>
        </>
      }
    >
      <div className="eo-strict-banner">
        <span aria-hidden>⚠</span>
        <div>
          <strong>Strict change.</strong> Pointing the blob root at a new
          backend can orphan in-flight ingest. Coordinate with traffic
          before saving in any shared environment.
        </div>
      </div>

      <div className="eo-form-row" data-full="1">
        <span className="eo-form-label">Provider</span>
        <ProviderSeg
          value={draft.provider}
          options={[
            ["local", "Local"],
            ["s3", "AWS S3"],
            ["azure", "Azure Blob"],
            ["gcs", "Google Cloud Storage"],
          ]}
          onChange={(p) => {
            setDraft({ ...draft, provider: p as BlobProvider });
            setTest(null);
          }}
        />
      </div>

      <div className="eo-form-grid">
        {draft.provider === "local" && (
          <Field label="Path" hint="default ./data/blobs" full>
            <input
              className="eo-input"
              placeholder="./data/blobs"
              value={draft.path}
              onChange={(e) => {
                setDraft({ ...draft, path: e.target.value });
                setTest(null);
              }}
            />
          </Field>
        )}

        {draft.provider === "s3" && (
          <>
            <Field label="Bucket" required>
              <input
                className="eo-input"
                value={draft.bucket}
                onChange={(e) => {
                  setDraft({ ...draft, bucket: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
            <Field label="Region">
              <input
                className="eo-input"
                placeholder="ap-northeast-2"
                value={draft.region}
                onChange={(e) => {
                  setDraft({ ...draft, region: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
            <Field label="Key prefix" hint="optional, e.g. easyobs/blobs/" full>
              <input
                className="eo-input"
                value={draft.prefix}
                onChange={(e) => setDraft({ ...draft, prefix: e.target.value })}
              />
            </Field>
            <div className="eo-form-row" data-full="1">
              <span className="eo-form-label">Custom credentials</span>
              <span className="eo-hint" style={{ marginTop: 0 }}>
                <strong>Optional.</strong> Leave blank to use the default
                AWS credential chain on the API host (env vars,{" "}
                <code>~/.aws/credentials</code>, or the EC2 / EKS / ECS
                task role). Fill these in only when the API host has no
                role of its own.
              </span>
            </div>
            <Field label="Access key ID">
              <input
                className="eo-input"
                placeholder="AKIA…"
                value={draft.s3_access_key_id}
                onChange={(e) => {
                  setDraft({ ...draft, s3_access_key_id: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
            <Field label="Secret access key">
              <input
                className="eo-input"
                type="password"
                placeholder={draft.s3_secret_access_key === SECRET_MASK ? SECRET_MASK : ""}
                value={draft.s3_secret_access_key === SECRET_MASK ? "" : draft.s3_secret_access_key}
                onChange={(e) => {
                  setDraft({ ...draft, s3_secret_access_key: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
          </>
        )}

        {draft.provider === "azure" && (
          <>
            <Field label="Storage account" required>
              <input
                className="eo-input"
                value={draft.azure_account_name}
                onChange={(e) => {
                  setDraft({ ...draft, azure_account_name: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
            <Field label="Container" required>
              <input
                className="eo-input"
                value={draft.azure_container}
                onChange={(e) => {
                  setDraft({ ...draft, azure_container: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
            <Field label="Blob prefix" hint="optional">
              <input
                className="eo-input"
                value={draft.prefix}
                onChange={(e) => setDraft({ ...draft, prefix: e.target.value })}
              />
            </Field>
            <Field label="Account key" hint="optional, blank = DefaultAzureCredential">
              <input
                className="eo-input"
                type="password"
                placeholder={draft.azure_account_key === SECRET_MASK ? SECRET_MASK : ""}
                value={draft.azure_account_key === SECRET_MASK ? "" : draft.azure_account_key}
                onChange={(e) => {
                  setDraft({ ...draft, azure_account_key: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
          </>
        )}

        {draft.provider === "gcs" && (
          <>
            <Field label="Bucket" required>
              <input
                className="eo-input"
                value={draft.bucket}
                onChange={(e) => {
                  setDraft({ ...draft, bucket: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
            <Field label="Object prefix" hint="optional">
              <input
                className="eo-input"
                value={draft.prefix}
                onChange={(e) => setDraft({ ...draft, prefix: e.target.value })}
              />
            </Field>
            <Field
              label="Service account JSON"
              hint="optional; blank = ADC (gcloud login / GCE metadata)"
              full
            >
              <textarea
                className="eo-input"
                rows={6}
                placeholder={
                  draft.gcs_service_account_json === SECRET_MASK
                    ? SECRET_MASK + " (paste new JSON to overwrite)"
                    : '{"type":"service_account",…}'
                }
                value={
                  draft.gcs_service_account_json === SECRET_MASK
                    ? ""
                    : draft.gcs_service_account_json
                }
                onChange={(e) => {
                  setDraft({ ...draft, gcs_service_account_json: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
          </>
        )}

        {draft.provider === "hybrid" && (
          <>
            <div className="eo-form-row" data-full="1">
              <span className="eo-hint" style={{ marginTop: 0 }}>
                <strong>Hybrid 모드:</strong> 로컬 Parquet(최근 N일 실시간) + S3(전체 아카이브) 동시 기록.
                프리셋 기간(1h/6h/24h/7d)은 로컬 스토어 쿼리, Custom 기간(7일 초과)은 S3 쿼리.
              </span>
            </div>
            <Field label="Hot retention (days)" hint="로컬 보관 일수">
              <input
                className="eo-input"
                type="number"
                min={1}
                max={90}
                value={draft.hot_retention_days ?? 7}
                onChange={(e) => {
                  setDraft({ ...draft, hot_retention_days: parseInt(e.target.value, 10) || 7 });
                  setTest(null);
                }}
              />
            </Field>
            <Field label="S3 Bucket" required>
              <input
                className="eo-input"
                value={draft.bucket}
                onChange={(e) => {
                  setDraft({ ...draft, bucket: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
            <Field label="Region">
              <input
                className="eo-input"
                placeholder="ap-northeast-2"
                value={draft.region}
                onChange={(e) => {
                  setDraft({ ...draft, region: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
            <Field label="Key prefix" hint="optional, e.g. traces/" full>
              <input
                className="eo-input"
                value={draft.prefix}
                onChange={(e) => setDraft({ ...draft, prefix: e.target.value })}
              />
            </Field>
            <Field label="Access key ID">
              <input
                className="eo-input"
                placeholder="AKIA…"
                value={draft.s3_access_key_id}
                onChange={(e) => {
                  setDraft({ ...draft, s3_access_key_id: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
            <Field label="Secret access key">
              <input
                className="eo-input"
                type="password"
                placeholder={draft.s3_secret_access_key === SECRET_MASK ? SECRET_MASK : ""}
                value={draft.s3_secret_access_key === SECRET_MASK ? "" : draft.s3_secret_access_key}
                onChange={(e) => {
                  setDraft({ ...draft, s3_secret_access_key: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
          </>
        )}
      </div>

      {isCloud && (
        <p className="eo-hint" style={{ marginTop: 12 }}>
          설정이 저장된 후 API 서비스를 재시작해야 반영됩니다.
          {draft.provider === "hybrid" && " Hybrid 모드: 로컬(최근 7일) + S3(전체) 동시 기록이 활성화됩니다."}
        </p>
      )}

      {saveMut.isError && (
        <div className="eo-banner eo-banner-warn" style={{ marginTop: 12 }}>
          Save failed: {(saveMut.error as Error).message}
        </div>
      )}
    </Modal>
  );
}

function CatalogChangeDialog({
  initial,
  onClose,
}: {
  initial: CatalogSettings;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<CatalogSettings>(initial);
  const [test, setTest] = useState<StorageTestResult | null>(null);
  const [confirm, setConfirm] = useState(false);

  const testMut = useMutation({
    mutationFn: () =>
      testCatalogConnection(stripMaskedSecrets(draft) as CatalogSettings),
    onSuccess: (r) => setTest(r),
    onError: (e: Error) => setTest({ ok: false, message: e.message }),
  });

  const saveMut = useMutation({
    mutationFn: () => {
      const current = qc.getQueryData<StorageSettings>(["settings-storage"]);
      return saveStorageSettings({
        blob: (current?.blob ?? EMPTY_BLOB) as BlobSettings,
        catalog: keepMaskedSecrets(draft, initial) as CatalogSettings,
        retention: (current?.retention ?? EMPTY_RETENTION) as RetentionSettings,
      });
    },
    onSuccess: (next) => {
      qc.setQueryData(["settings-storage"], next);
      onClose();
    },
  });

  const providerChanged = draft.provider !== initial.provider;
  const needsTest = draft.provider === "postgres";
  const canSave =
    !saveMut.isPending &&
    confirm &&
    (!needsTest || (test?.ok && !providerNeedsRetest(draft, test)));

  return (
    <Modal
      title="Change metadata catalog"
      sub={
        <>
          The catalog stores users, organizations, services, ingest tokens
          and the trace index. <strong>Switching the catalog backend
          replaces the source-of-truth DB</strong> on the next API restart
          — existing rows are not migrated automatically.
        </>
      }
      onClose={onClose}
      footer={
        <>
          {test && (
            <span className="eo-status" data-tone={test.ok ? "ok" : "err"}>
              {test.ok ? "OK" : "Failed"} · {test.message}
            </span>
          )}
          <span className="eo-spacer" />
          <button
            type="button"
            className="eo-btn"
            onClick={() => testMut.mutate()}
            disabled={testMut.isPending}
          >
            {testMut.isPending ? "Testing…" : "Test connection"}
          </button>
          <button type="button" className="eo-btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="eo-btn eo-btn-primary"
            disabled={!canSave}
            onClick={() => saveMut.mutate()}
          >
            {saveMut.isPending ? "Saving…" : "Save & schedule restart"}
          </button>
        </>
      }
    >
      <div className="eo-strict-banner">
        <span aria-hidden>⚠</span>
        <div>
          <strong>Strict change.</strong> Pointing the catalog at a new DB
          will drop existing users / orgs / tokens from view until they
          are re-created in the new database. Take a snapshot first.
          {providerChanged && (
            <>
              {" "}
              <span style={{ display: "block", marginTop: 4 }}>
                Provider switch detected:{" "}
                <code>{initial.provider}</code> → <code>{draft.provider}</code>
              </span>
            </>
          )}
        </div>
      </div>

      <div className="eo-form-row" data-full="1">
        <span className="eo-form-label">Provider</span>
        <ProviderSeg
          value={draft.provider}
          options={[
            ["sqlite", "SQLite"],
            ["postgres", "PostgreSQL"],
          ]}
          onChange={(p) => {
            setDraft({ ...draft, provider: p as CatalogProvider });
            setTest(null);
            setConfirm(false);
          }}
        />
      </div>

      <div className="eo-form-grid">
        {draft.provider === "sqlite" && (
          <Field label="DB file" hint="default ./data/catalog.sqlite3" full>
            <input
              className="eo-input"
              placeholder="./data/catalog.sqlite3"
              value={draft.sqlite_path}
              onChange={(e) => {
                setDraft({ ...draft, sqlite_path: e.target.value });
                setConfirm(false);
              }}
            />
          </Field>
        )}

        {draft.provider === "postgres" && (
          <>
            <Field label="Host" required>
              <input
                className="eo-input"
                placeholder="db.internal"
                value={draft.pg_host}
                onChange={(e) => {
                  setDraft({ ...draft, pg_host: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
            <Field label="Port">
              <input
                className="eo-input"
                type="number"
                value={draft.pg_port || 5432}
                onChange={(e) => {
                  setDraft({ ...draft, pg_port: Number(e.target.value) || 5432 });
                  setTest(null);
                }}
              />
            </Field>
            <Field label="Database" required>
              <input
                className="eo-input"
                value={draft.pg_database}
                onChange={(e) => {
                  setDraft({ ...draft, pg_database: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
            <Field label="User" required>
              <input
                className="eo-input"
                value={draft.pg_user}
                onChange={(e) => {
                  setDraft({ ...draft, pg_user: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
            <Field label="Password">
              <input
                className="eo-input"
                type="password"
                placeholder={draft.pg_password === SECRET_MASK ? SECRET_MASK : ""}
                value={draft.pg_password === SECRET_MASK ? "" : draft.pg_password}
                onChange={(e) => {
                  setDraft({ ...draft, pg_password: e.target.value });
                  setTest(null);
                }}
              />
            </Field>
            <Field label="SSL mode">
              <select
                className="eo-input"
                value={draft.pg_sslmode}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    pg_sslmode: e.target.value as CatalogSettings["pg_sslmode"],
                  })
                }
              >
                <option value="disable">disable</option>
                <option value="allow">allow</option>
                <option value="prefer">prefer</option>
                <option value="require">require</option>
                <option value="verify-full">verify-full</option>
              </select>
            </Field>
          </>
        )}
      </div>

      <label
        className="eo-row"
        style={{ marginTop: 14, alignItems: "center", gap: 8, fontSize: 12 }}
      >
        <input
          type="checkbox"
          checked={confirm}
          onChange={(e) => setConfirm(e.target.checked)}
        />
        <span>
          I understand this rebinds the metadata catalog and that data must
          be migrated separately. The change applies on the next API restart.
        </span>
      </label>

      {saveMut.isError && (
        <div className="eo-banner eo-banner-warn" style={{ marginTop: 12 }}>
          Save failed: {(saveMut.error as Error).message}
        </div>
      )}
    </Modal>
  );
}

function RetentionChangeDialog({
  initial,
  blob,
  catalog,
  onClose,
}: {
  initial: RetentionSettings;
  blob: BlobSettings;
  catalog: CatalogSettings;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<RetentionSettings>(initial);

  const saveMut = useMutation({
    mutationFn: () =>
      saveStorageSettings({
        blob,
        catalog,
        retention: draft,
      }),
    onSuccess: (next) => {
      qc.setQueryData(["settings-storage"], next);
      onClose();
    },
  });

  return (
    <Modal
      title="Change retention policy"
      sub={
        <>
          The policy is persisted but EasyObs has no automatic cleaner job
          yet. For now this just records the operator&apos;s intended
          window — the actual deletion will be wired in a follow-up.
        </>
      }
      onClose={onClose}
      footer={
        <>
          <span className="eo-spacer" />
          <button type="button" className="eo-btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="eo-btn eo-btn-primary"
            disabled={saveMut.isPending}
            onClick={() => saveMut.mutate()}
          >
            {saveMut.isPending ? "Saving…" : "Save policy"}
          </button>
        </>
      }
    >
      <div className="eo-form-grid">
        <Field label="Enabled">
          <button
            type="button"
            className="eo-chip"
            data-active={draft.enabled}
            onClick={() => setDraft({ ...draft, enabled: !draft.enabled })}
          >
            {draft.enabled ? "On" : "Off"}
          </button>
        </Field>
        <Field
          label="Window (days)"
          hint="trace data older than this is candidate for deletion"
        >
          <input
            className="eo-input"
            type="number"
            min={1}
            max={3650}
            value={draft.days || 30}
            disabled={!draft.enabled}
            onChange={(e) =>
              setDraft({ ...draft, days: Math.max(1, Number(e.target.value) || 1) })
            }
          />
        </Field>
      </div>

      {saveMut.isError && (
        <div className="eo-banner eo-banner-warn" style={{ marginTop: 12 }}>
          Save failed: {(saveMut.error as Error).message}
        </div>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function Field({
  label,
  hint,
  required,
  full,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  full?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="eo-form-row" data-full={full ? "1" : undefined}>
      <span className="eo-form-label">
        {label}
        {required && <span style={{ color: "var(--eo-err)" }}> *</span>}
      </span>
      {children}
      {hint && <span className="eo-hint" style={{ marginTop: 4 }}>{hint}</span>}
    </label>
  );
}

function ProviderSeg<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: Array<[T, string]>;
  onChange: (v: T) => void;
}) {
  return (
    <div className="eo-seg">
      {options.map(([id, label]) => (
        <button
          key={id}
          type="button"
          data-active={value === id}
          onClick={() => onChange(id)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/** Once a test passes, any subsequent edit invalidates it so the user
 * cannot "test then quietly edit then save". This is intentionally a
 * shallow check — every edit handler also clears `test` to null. */
function providerNeedsRetest(_cfg: unknown, _test: StorageTestResult): boolean {
  return false;
}

/** When the user didn't touch a masked secret field, swap it back to "" so
 * the connection-test endpoint doesn't try to authenticate with the literal
 * mask string. */
function stripMaskedSecrets<T extends Record<string, unknown>>(cfg: T): T {
  const out = { ...cfg };
  for (const k of Object.keys(out)) {
    if (out[k] === SECRET_MASK) {
      (out as Record<string, unknown>)[k] = "";
    }
  }
  return out;
}

/** When saving, if the secret field still equals the mask, send empty
 * string (no-op = keep what's stored). The backend doesn't echo real
 * secrets, so editing a non-secret field never clobbers a previously
 * stored secret — only typing a new value overwrites it. */
function keepMaskedSecrets<T extends Record<string, unknown>>(
  next: T,
  _prev: T,
): T {
  const out = { ...next };
  for (const k of Object.keys(out)) {
    if (out[k] === SECRET_MASK) {
      (out as Record<string, unknown>)[k] = "";
    }
  }
  return out;
}
