"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import {
  consumeGoldenUpload,
  fetchUploadSchema,
  type GoldenSet,
  type UploadValidationResult,
  validateGoldenUpload,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n/context";

type Props = { set: GoldenSet; writable: boolean };

function humanHints(t: (key: string) => string): Record<string, string> {
  return {
    "L1.query_text": t("pages.golden.upload.hintL1Query"),
    "L1.intent": t("pages.golden.upload.hintL1Intent"),
    "L1.difficulty": t("pages.golden.upload.hintL1Difficulty"),
    "L1.language": t("pages.golden.upload.hintL1Language"),
    "L1.expected_tool": t("pages.golden.upload.hintL1ExpectedTool"),
    "L1.tags": t("pages.golden.upload.hintL1Tags"),
    "L2.relevant_doc_ids": t("pages.golden.upload.hintL2RelevantDocs"),
    "L2.must_have_chunks": t("pages.golden.upload.hintL2MustHaveChunks"),
    "L2.k_target": t("pages.golden.upload.hintL2KTarget"),
    "L3.expected_answer_text": t("pages.golden.upload.hintL3ExpectedAnswer"),
    "L3.must_include": t("pages.golden.upload.hintL3MustInclude"),
    "L3.must_not_include": t("pages.golden.upload.hintL3MustNotInclude"),
    "L3.citations_expected": t("pages.golden.upload.hintL3Citations"),
    "L3.schema": t("pages.golden.upload.hintL3Schema"),
  };
}

/** Golden Set Upload (CSV / JSONL / xlsx).
 *
 * - The client uploads the raw file; the backend sanitises and returns a
 *   preview. (We deliberately skip browser-side preview so the operator
 *   sees exactly what the server will persist — guarantees consistency.)
 * - [Preview] runs parsing + sanitisation only. [Insert] performs the
 *   actual INSERT into ``golden_items``.
 * - PII redaction and formula-injection prefixing happen on the server.
 */
export function GoldenUploadPanel({ set, writable }: Props) {
  const { t, tsub } = useI18n();
  const HUMAN_HINTS = humanHints(t);
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [hasHeader, setHasHeader] = useState(true);
  const [redactPii, setRedactPii] = useState(false);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<UploadValidationResult | null>(null);

  const schema = useQuery({
    queryKey: ["eval", "upload-schema"],
    queryFn: fetchUploadSchema,
    staleTime: 60_000,
  });

  const supportedPaths = useMemo(
    () => schema.data?.paths ?? Object.keys(HUMAN_HINTS),
    [schema.data],
  );

  const validate = useMutation({
    mutationFn: () => {
      if (!file) throw new Error(t("pages.golden.upload.pickFile"));
      if (Object.values(mapping).filter((v) => v).length === 0) {
        throw new Error(
          t("pages.golden.upload.mapAtLeastOne"),
        );
      }
      const cleanMapping: Record<string, string> = {};
      for (const [k, v] of Object.entries(mapping)) {
        if (v) cleanMapping[k] = v;
      }
      return validateGoldenUpload(set.id, {
        file,
        mapping: cleanMapping,
        hasHeader,
        redactPii,
      });
    },
    onSuccess: (r) => {
      setPreview(r);
      setError(null);
      setMapping((cur) => {
        if (Object.keys(cur).length > 0) return cur;
        const next: Record<string, string> = {};
        for (const h of r.headers) next[h] = "";
        return next;
      });
    },
    onError: (e: Error) => {
      setError(e.message);
      setPreview(null);
    },
  });

  const consume = useMutation({
    mutationFn: () => {
      if (!file) throw new Error(t("pages.golden.upload.pickFile"));
      const cleanMapping: Record<string, string> = {};
      for (const [k, v] of Object.entries(mapping)) {
        if (v) cleanMapping[k] = v;
      }
      return consumeGoldenUpload(set.id, {
        file,
        mapping: cleanMapping,
        hasHeader,
        redactPii,
      });
    },
    onSuccess: (r) => {
      setError(null);
      setPreview(null);
      setFile(null);
      setMapping({});
      qc.invalidateQueries({ queryKey: ["eval", "golden-items", set.id] });
      qc.invalidateQueries({ queryKey: ["eval", "golden-sets"] });
      alert(
        tsub("pages.golden.upload.insertedItems", { count: String(r.inserted) }),
      );
    },
    onError: (e: Error) => setError(e.message),
  });

  const onFileChange = (f: File | null) => {
    setFile(f);
    setPreview(null);
    setMapping({});
  };

  // Auto-validate once file + mapping are present so the operator
  // immediately sees what the server thinks of the file.
  useEffect(() => {
    if (!file) return;
    if (Object.values(mapping).filter((v) => v).length === 0) return;
    if (validate.isPending) return;
    const tid = window.setTimeout(() => validate.mutate(), 200);
    return () => window.clearTimeout(tid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file, JSON.stringify(mapping), hasHeader, redactPii]);

  return (
    <div className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">Upload (CSV / JSONL / xlsx)</h3>
        <span className="eo-card-sub">
          {t("pages.golden.upload.subtitle")}
        </span>
      </div>
      <p className="eo-mute" style={{ fontSize: 12, marginBottom: 8 }}>
        {t("pages.golden.upload.description")}
      </p>
      <div className="eo-grid-3" style={{ gap: 8 }}>
        <label className="eo-field">
          <span>{t("pages.golden.upload.file")}</span>
          <span className="eo-file">
            <span className="eo-file-button">
              <span aria-hidden="true">⇪</span>
              {t("pages.golden.upload.chooseFile")}
            </span>
            <span className="eo-file-name">
              {file ? file.name : t("pages.golden.upload.noFileSelected")}
            </span>
            <input
              type="file"
              accept=".csv,.jsonl,.xlsx"
              disabled={!writable}
              onChange={(e) => onFileChange(e.target.files?.[0] ?? null)}
            />
          </span>
        </label>
        <label className="eo-field" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={hasHeader}
            onChange={(e) => setHasHeader(e.target.checked)}
            disabled={!writable}
          />
          <span>{t("pages.golden.upload.firstRowHeader")}</span>
        </label>
        <label className="eo-field" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={redactPii}
            onChange={(e) => setRedactPii(e.target.checked)}
            disabled={!writable}
          />
          <span>{t("pages.golden.upload.maskPii")}</span>
        </label>
      </div>

      {preview && (
        <>
          <div className="eo-divider" style={{ margin: "10px 0" }} />
          <strong style={{ fontSize: 12 }}>{t("pages.golden.upload.columnMapping")}</strong>
          <div className="eo-table-wrap" style={{ marginTop: 4 }}>
            <table className="eo-table">
              <thead>
                <tr>
                  <th>{t("pages.golden.upload.fileColumn")}</th>
                  <th>{t("pages.golden.upload.goldenItemField")}</th>
                </tr>
              </thead>
              <tbody>
                {preview.headers.map((h) => (
                  <tr key={h}>
                    <td className="mono">{h}</td>
                    <td>
                      <select
                        value={mapping[h] ?? ""}
                        onChange={(e) =>
                          setMapping((cur) => ({ ...cur, [h]: e.target.value }))
                        }
                        disabled={!writable}
                      >
                        <option value="">{t("pages.golden.upload.dontMap")}</option>
                        {supportedPaths.map((p) => (
                          <option key={p} value={p}>
                            {HUMAN_HINTS[p] ?? p}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div
            className="eo-mute"
            style={{ fontSize: 12, marginTop: 6, display: "flex", gap: 12 }}
          >
            <span>file kind: {preview.fileKind}</span>
            <span>valid: {preview.validCount}</span>
            <span>skipped: {preview.skippedCount}</span>
            {preview.truncated && <span>⚠ truncated</span>}
          </div>
          {preview.issues.length > 0 && (
            <details style={{ marginTop: 6 }}>
              <summary>issues ({preview.issues.length})</summary>
              <ul style={{ paddingLeft: 16, fontSize: 12 }}>
                {preview.issues.slice(0, 50).map((iss, i) => (
                  <li key={i}>{iss}</li>
                ))}
              </ul>
            </details>
          )}
          {preview.sampleRows.length > 0 && (
            <details style={{ marginTop: 6 }}>
              <summary>
                {tsub("pages.golden.upload.previewRows", { count: String(preview.sampleRows.length) })}
              </summary>
              <pre
                style={{
                  fontSize: 11,
                  maxHeight: 220,
                  overflow: "auto",
                  background: "var(--eo-bg-2)",
                  padding: 8,
                }}
              >
                {JSON.stringify(preview.sampleRows.slice(0, 20), null, 2)}
              </pre>
            </details>
          )}
        </>
      )}

      {error && (
        <div className="eo-empty" style={{ color: "var(--eo-err)" }}>
          {error}
        </div>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button
          type="button"
          className="eo-btn"
          disabled={!writable || !file || validate.isPending}
          onClick={() => validate.mutate()}
        >
          {validate.isPending
            ? t("pages.golden.upload.validating")
            : t("pages.golden.upload.preview")}
        </button>
        <button
          type="button"
          className="eo-btn eo-btn-primary"
          disabled={
            !writable ||
            !file ||
            !preview ||
            preview.validCount === 0 ||
            consume.isPending
          }
          onClick={() => consume.mutate()}
        >
          {consume.isPending
            ? t("pages.golden.upload.inserting")
            : tsub("pages.golden.upload.registerItems", { count: String(preview?.validCount ?? 0) })}
        </button>
      </div>
    </div>
  );
}
