"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { createOrganization, fetchOrganizations } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtRel } from "@/lib/format";
import { useI18n } from "@/lib/i18n/context";

export default function OrganizationsPage() {
  const { t } = useI18n();
  const auth = useAuth();
  const qc = useQueryClient();
  const orgsQ = useQuery({ queryKey: ["organizations"], queryFn: fetchOrganizations });

  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const createMut = useMutation({
    mutationFn: (n: string) => createOrganization(n),
    onSuccess: () => {
      setName("");
      setError(null);
      qc.invalidateQueries({ queryKey: ["organizations"] });
    },
    onError: (e) => setError(e instanceof Error ? e.message : "create failed"),
  });

  const isPlatformAdmin = auth.isPlatformAdmin;

  return (
    <>
      <div className="eo-page-head">
        <div>
          <h1 className="eo-page-title">{t("pages.orgs.title")}</h1>
          <p className="eo-page-lede">
            {t(isPlatformAdmin ? "pages.orgs.ledeAdmin" : "pages.orgs.ledeMember")}
          </p>
        </div>
      </div>

      {isPlatformAdmin && (
        <section className="eo-card">
          <div className="eo-card-h">
            <h3 className="eo-card-title">Create organization</h3>
            <span className="eo-card-sub">Administrator org members only</span>
          </div>
          <div className="eo-token-new">
            <input
              className="eo-input"
              type="text"
              placeholder="organization name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <button
              type="button"
              className="eo-btn eo-btn-primary"
              disabled={!name.trim() || createMut.isPending}
              onClick={() => createMut.mutate(name.trim())}
            >
              {createMut.isPending ? "creating…" : "Create"}
            </button>
          </div>
          {error && <div className="eo-auth-error" style={{ marginTop: 8 }}>{error}</div>}
        </section>
      )}

      <section className="eo-card">
        <div className="eo-card-h">
          <h3 className="eo-card-title">All organizations</h3>
          <span className="eo-card-sub">{orgsQ.data?.length ?? 0} total</span>
        </div>
        <table className="eo-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Slug</th>
              <th>Default</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {orgsQ.isLoading && (
              <tr>
                <td colSpan={4} className="eo-empty">
                  loading…
                </td>
              </tr>
            )}
            {!orgsQ.isLoading && (orgsQ.data ?? []).length === 0 && (
              <tr>
                <td colSpan={4} className="eo-empty">
                  no organizations visible to you yet.
                </td>
              </tr>
            )}
            {(orgsQ.data ?? []).map((o) => (
              <tr key={o.id}>
                <td>
                  <Link href={`/workspace/setup/organizations/${o.id}`}>
                    <strong>{o.name}</strong>
                  </Link>
                </td>
                <td>
                  <code>{o.slug}</code>
                </td>
                <td>
                  {o.isDefault ? (
                    <span className="eo-pill-role" data-role="SA">
                      default
                    </span>
                  ) : (
                    <span className="eo-mute">—</span>
                  )}
                </td>
                <td>{fmtRel(o.createdAt)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
