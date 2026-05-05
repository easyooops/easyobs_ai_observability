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
import { useBilingual } from "@/lib/i18n/bilingual";

type Props = { set: GoldenSet; writable: boolean };

function humanHints(b: (en: string, ko: string) => string): Record<string, string> {
  return {
    "L1.query_text": b("L1 — user query (required)", "L1 — 사용자 query (필수)"),
    "L1.intent": b("L1 — intent slug", "L1 — intent slug"),
    "L1.difficulty": b("L1 — easy/medium/hard", "L1 — easy/medium/hard"),
    "L1.language": b("L1 — language code", "L1 — 언어 코드"),
    "L1.expected_tool": b("L1 — expected tool", "L1 — 기대 도구"),
    "L1.tags": b("L1 — tags (comma)", "L1 — 태그 (comma)"),
    "L2.relevant_doc_ids": b(
      "L2 — relevant doc ids (comma)",
      "L2 — 정답 문서 id (comma)",
    ),
    "L2.must_have_chunks": b(
      "L2 — must-have chunks (comma)",
      "L2 — 필수 chunk (comma)",
    ),
    "L2.k_target": b("L2 — retrieval k target", "L2 — 검색 k 목표"),
    "L3.expected_answer_text": b(
      "L3 — expected answer text",
      "L3 — 정답 텍스트",
    ),
    "L3.must_include": b(
      "L3 — must include (comma)",
      "L3 — 필수 포함 어구 (comma)",
    ),
    "L3.must_not_include": b(
      "L3 — must not include (comma)",
      "L3 — 금지 어구 (comma)",
    ),
    "L3.citations_expected": b(
      "L3 — expected citation doc ids (comma)",
      "L3 — 인용 doc id (comma)",
    ),
    "L3.schema": b("L3 — JSON schema", "L3 — JSON schema"),
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
  const b = useBilingual();
  const HUMAN_HINTS = humanHints(b);
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
      if (!file) throw new Error(b("Pick a file first.", "파일을 선택하세요"));
      if (Object.values(mapping).filter((v) => v).length === 0) {
        throw new Error(
          b(
            "Map at least one column.",
            "최소 1개 이상의 컬럼 매핑이 필요합니다",
          ),
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
      if (!file) throw new Error(b("Pick a file first.", "파일을 선택하세요"));
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
        b(
          `${r.inserted} item(s) registered as candidates.`,
          `${r.inserted} 개 항목이 candidate 로 등록되었습니다.`,
        ),
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
    const t = window.setTimeout(() => validate.mutate(), 200);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file, JSON.stringify(mapping), hasHeader, redactPii]);

  return (
    <div className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">Upload (CSV / JSONL / xlsx)</h3>
        <span className="eo-card-sub">
          {b("bulk-register as candidates", "candidate 로 일괄 등록")}
        </span>
      </div>
      <p className="eo-mute" style={{ fontSize: 12, marginBottom: 8 }}>
        {b(
          "When you upload, the server parses + sanitises the file (formula-injection prefix, external refs ignored) and returns a preview. Confirm the mapping and press [Register].",
          "파일을 업로드하면 서버에서 파싱·sanitise (수식 인젝션 prefix, 외부참조 무시) 후 미리보기로 보여드립니다. 매핑이 만족스러우면 [등록] 버튼으로 확정합니다.",
        )}
      </p>
      <div className="eo-grid-3" style={{ gap: 8 }}>
        <label className="eo-field">
          <span>{b("File", "파일")}</span>
          <span className="eo-file">
            <span className="eo-file-button">
              <span aria-hidden="true">⇪</span>
              {b("Choose file…", "파일 선택…")}
            </span>
            <span className="eo-file-name">
              {file ? file.name : b("no file selected", "선택된 파일 없음")}
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
          <span>{b("First row is header", "첫 행이 헤더")}</span>
        </label>
        <label className="eo-field" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={redactPii}
            onChange={(e) => setRedactPii(e.target.checked)}
            disabled={!writable}
          />
          <span>{b("Mask PII (email/phone/SSN)", "PII 마스킹 (이메일·전화·주민번호)")}</span>
        </label>
      </div>

      {preview && (
        <>
          <div className="eo-divider" style={{ margin: "10px 0" }} />
          <strong style={{ fontSize: 12 }}>{b("Column mapping", "컬럼 매핑")}</strong>
          <div className="eo-table-wrap" style={{ marginTop: 4 }}>
            <table className="eo-table">
              <thead>
                <tr>
                  <th>{b("File column", "파일 컬럼")}</th>
                  <th>{b("Golden Item field", "Golden Item 필드")}</th>
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
                        <option value="">{b("— don't map —", "— 매핑 안 함 —")}</option>
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
                {b(
                  `Preview (${preview.sampleRows.length} rows)`,
                  `미리보기 (${preview.sampleRows.length}행)`,
                )}
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
            ? b("Validating…", "검증 중…")
            : b("Preview", "미리보기")}
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
            ? b("Inserting…", "등록 중…")
            : b(
                `Register ${preview?.validCount ?? 0} item(s)`,
                `${preview?.validCount ?? 0} 개 등록`,
              )}
        </button>
      </div>
    </div>
  );
}
