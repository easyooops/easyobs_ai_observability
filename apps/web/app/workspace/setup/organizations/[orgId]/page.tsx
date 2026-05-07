"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { use, useState } from "react";
import {
  createService,
  createServiceToken,
  deleteService,
  fetchMemberServices,
  fetchOrganizations,
  fetchOrgMembers,
  fetchOrgServices,
  fetchServiceTokens,
  removeMember,
  revokeServiceToken,
  setMemberServices,
  updateMember,
  type IngestTokenCreated,
  type ServiceRow,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtRel } from "@/lib/format";
import { useI18n } from "@/lib/i18n/context";
import { AlarmsTab } from "./alarms-tab";

type Tab = "members" | "services" | "alarms";

export default function OrgDetailPage({
  params,
}: {
  params: Promise<{ orgId: string }>;
}) {
  const { orgId } = use(params);
  const { t } = useI18n();
  const auth = useAuth();
  const orgsQ = useQuery({ queryKey: ["organizations"], queryFn: fetchOrganizations });
  const org = (orgsQ.data ?? []).find((o) => o.id === orgId);

  const [tab, setTab] = useState<Tab>("members");
  const [selectedService, setSelectedService] = useState<ServiceRow | null>(null);

  return (
    <>
      <div className="eo-page-head">
        <div>
          <h1 className="eo-page-title">{org?.name ?? t("pages.orgDetail.titleFallback")}</h1>
          <p className="eo-page-lede">
            {org?.isDefault
              ? t("pages.orgDetail.ledeDefault")
              : t("pages.orgDetail.ledeTenant")}
          </p>
        </div>
      </div>

      <div className="eo-tab-bar">
        <button
          type="button"
          className="eo-tab"
          data-active={tab === "members"}
          onClick={() => setTab("members")}
        >
          {t("pages.orgDetail.tabMembers")}
        </button>
        <button
          type="button"
          className="eo-tab"
          data-active={tab === "services"}
          onClick={() => setTab("services")}
        >
          {t("pages.orgDetail.tabServices")}
        </button>
        <button
          type="button"
          className="eo-tab"
          data-active={tab === "alarms"}
          onClick={() => setTab("alarms")}
        >
          {t("pages.orgDetail.tabAlarms")}
        </button>
      </div>

      {tab === "members" && <MembersTab orgId={orgId} />}
      {tab === "services" && (
        <ServicesTab
          orgId={orgId}
          selected={selectedService}
          onSelect={setSelectedService}
        />
      )}
      {tab === "alarms" && <AlarmsTab orgId={orgId} />}

      {tab === "services" && selectedService && (
        <TokensCard service={selectedService} />
      )}

      {auth.role !== "SA" && auth.role !== "PO" && (
        <div className="eo-empty" style={{ marginTop: 12 }}>
          {t("pages.orgDetail.readOnlyHint")}
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Members tab
// ---------------------------------------------------------------------------

function MembersTab({ orgId }: { orgId: string }) {
  const { t, tsub } = useI18n();
  const auth = useAuth();
  const qc = useQueryClient();
  const membersQ = useQuery({
    queryKey: ["org-members", orgId],
    queryFn: () => fetchOrgMembers(orgId),
  });
  const servicesQ = useQuery({
    queryKey: ["org-services", orgId],
    queryFn: () => fetchOrgServices(orgId),
  });
  const canManage = auth.isPlatformAdmin || auth.role === "PO";

  const updateMut = useMutation({
    mutationFn: (args: {
      userId: string;
      body: { status?: "approved" | "rejected" | "pending"; role?: "PO" | "DV" };
    }) => updateMember(orgId, args.userId, args.body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["org-members", orgId] }),
  });

  const removeMut = useMutation({
    mutationFn: (userId: string) => removeMember(orgId, userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["org-members", orgId] }),
  });

  return (
    <section className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">{t("pages.orgDetail.membersTitle")}</h3>
        <span className="eo-card-sub">
          {tsub("pages.orgDetail.membersSub", { n: String(membersQ.data?.length ?? 0) })}
        </span>
      </div>
      <table className="eo-table">
        <thead>
          <tr>
            <th>{t("pages.orgDetail.colUser")}</th>
            <th>{t("pages.orgDetail.colRole")}</th>
            <th>{t("pages.orgDetail.colStatus")}</th>
            <th>{t("pages.orgDetail.colRequested")}</th>
            <th>{t("pages.orgDetail.colServiceAssignments")}</th>
            <th className="eo-col-right">{t("pages.orgDetail.colActions")}</th>
          </tr>
        </thead>
        <tbody>
          {membersQ.isLoading && (
            <tr>
              <td colSpan={6} className="eo-empty">{t("pages.orgDetail.loading")}</td>
            </tr>
          )}
          {!membersQ.isLoading && (membersQ.data ?? []).length === 0 && (
            <tr>
              <td colSpan={6} className="eo-empty">{t("pages.orgDetail.noMembers")}</td>
            </tr>
          )}
          {(membersQ.data ?? []).map((m) => {
            // The bootstrapped super admin must never be editable through
            // the regular membership endpoints (the server also rejects it),
            // so lock every control on the row and surface a small badge.
            const locked = m.userIsSuperAdmin;
            return (
              <tr key={m.userId}>
                <td>
                  <strong>{m.userDisplayName || m.userEmail}</strong>
                  {locked && (
                    <span
                      className="eo-pill"
                      style={{ marginLeft: 6, fontSize: 9.5 }}
                      title="Bootstrapped super admin — read-only"
                    >
                      SA
                    </span>
                  )}
                  <div className="eo-mute" style={{ fontSize: 11 }}>
                    {m.userEmail}
                  </div>
                </td>
                <td>
                  {locked ? (
                    <span className="eo-mute" style={{ fontSize: 12 }}>
                      Super Admin
                    </span>
                  ) : (
                    <select
                      className="eo-input"
                      value={m.role}
                      disabled={!canManage}
                      onChange={(e) =>
                        updateMut.mutate({
                          userId: m.userId,
                          body: { role: e.target.value as "PO" | "DV" },
                        })
                      }
                    >
                      <option value="DV">DV</option>
                      <option value="PO">PO</option>
                    </select>
                  )}
                </td>
                <td>
                  <span
                    className="eo-pill-role"
                    data-role={
                      locked || m.status === "approved" ? "PO" : "DV"
                    }
                  >
                    {locked ? "approved" : m.status}
                  </span>
                </td>
                <td>{fmtRel(m.requestedAt)}</td>
                <td>
                  {locked ? (
                    <span className="eo-mute" style={{ fontSize: 12 }}>
                      SA has access to every service in every org
                    </span>
                  ) : m.role === "DV" ? (
                    <ServiceAssignmentsEditor
                      orgId={orgId}
                      userId={m.userId}
                      services={servicesQ.data ?? []}
                      canManage={!!canManage}
                    />
                  ) : (
                    <span className="eo-mute" style={{ fontSize: 12 }}>
                      PO has access to all services in the org
                    </span>
                  )}
                </td>
                <td className="eo-col-right">
                  {canManage && !locked && (
                    <div className="eo-row-actions">
                      {m.status !== "approved" && (
                        <button
                          type="button"
                          className="eo-btn eo-btn-primary"
                          onClick={() =>
                            updateMut.mutate({
                              userId: m.userId,
                              body: { status: "approved" },
                            })
                          }
                        >
                          Approve
                        </button>
                      )}
                      {m.status === "approved" && (
                        <button
                          type="button"
                          className="eo-btn eo-btn-ghost"
                          onClick={() =>
                            updateMut.mutate({
                              userId: m.userId,
                              body: { status: "rejected" },
                            })
                          }
                        >
                          Suspend
                        </button>
                      )}
                      <button
                        type="button"
                        className="eo-btn eo-btn-ghost"
                        onClick={() => {
                          if (
                            confirm(
                              `Remove ${m.userEmail} from this organization?`,
                            )
                          ) {
                            removeMut.mutate(m.userId);
                          }
                        }}
                      >
                        Remove
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function ServiceAssignmentsEditor({
  orgId,
  userId,
  services,
  canManage,
}: {
  orgId: string;
  userId: string;
  services: ServiceRow[];
  canManage: boolean;
}) {
  const qc = useQueryClient();
  const assignedQ = useQuery({
    queryKey: ["member-services", orgId, userId],
    queryFn: () => fetchMemberServices(orgId, userId),
  });
  const setMut = useMutation({
    mutationFn: (ids: string[]) => setMemberServices(orgId, userId, ids),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["member-services", orgId, userId] }),
  });
  const assigned = new Set(assignedQ.data ?? []);

  if (services.length === 0) {
    return <span className="eo-mute" style={{ fontSize: 12 }}>(no services)</span>;
  }

  const toggle = (id: string) => {
    const next = new Set(assigned);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    setMut.mutate(Array.from(next));
  };

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
      {services.map((s) => {
        const on = assigned.has(s.id);
        return (
          <button
            key={s.id}
            type="button"
            className="eo-chip"
            data-active={on}
            disabled={!canManage}
            onClick={() => toggle(s.id)}
          >
            {s.name}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Services tab
// ---------------------------------------------------------------------------

function ServicesTab({
  orgId,
  selected,
  onSelect,
}: {
  orgId: string;
  selected: ServiceRow | null;
  onSelect: (s: ServiceRow | null) => void;
}) {
  const { t, tsub } = useI18n();
  const auth = useAuth();
  const qc = useQueryClient();
  const servicesQ = useQuery({
    queryKey: ["org-services", orgId],
    queryFn: () => fetchOrgServices(orgId),
  });
  const canManage = auth.isPlatformAdmin || auth.role === "PO";

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const createMut = useMutation({
    mutationFn: () => createService(orgId, { name, description }),
    onSuccess: () => {
      setName("");
      setDescription("");
      setError(null);
      qc.invalidateQueries({ queryKey: ["org-services", orgId] });
    },
    onError: (e) =>
      setError(
        e instanceof Error
          ? e.message
          : t("pages.orgDetail.createFailed"),
      ),
  });
  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteService(orgId, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["org-services", orgId] });
      onSelect(null);
    },
  });

  return (
    <section className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">{t("pages.orgDetail.servicesTitle")}</h3>
        <span className="eo-card-sub">
          {tsub("pages.orgDetail.servicesSub", {
            n: String(servicesQ.data?.length ?? 0),
          })}
        </span>
      </div>

      {canManage && (
        // Three explicit grid tracks (name | description | button) keep the
        // primary action aligned with the inputs at any width and stop the
        // button from stretching across a phantom second row — the default
        // .eo-token-new template (1fr auto) only has room for two children.
        <div
          className="eo-token-new eo-service-form-grid"
        >
          <input
            className="eo-input"
            type="text"
            placeholder={t("pages.orgDetail.phServiceName")}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <input
            className="eo-input"
            type="text"
            placeholder={t("pages.orgDetail.phServiceDescription")}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <button
            type="button"
            className="eo-btn eo-btn-primary"
            disabled={!name.trim() || createMut.isPending}
            onClick={() => createMut.mutate()}
            style={{ whiteSpace: "nowrap" }}
          >
            {createMut.isPending
              ? t("pages.orgDetail.creating")
              : t("pages.orgDetail.createService")}
          </button>
        </div>
      )}
      {error && <div className="eo-auth-error" style={{ marginTop: 8 }}>{error}</div>}

      <table className="eo-table" style={{ marginTop: 10 }}>
        <thead>
          <tr>
            <th>{t("pages.orgDetail.colName")}</th>
            <th>{t("pages.orgDetail.colSlug")}</th>
            <th>{t("pages.orgDetail.colDescription")}</th>
            <th>{t("pages.orgDetail.colCreated")}</th>
            {canManage && (
              <th className="eo-col-right">{t("pages.orgDetail.colActions")}</th>
            )}
          </tr>
        </thead>
        <tbody>
          {servicesQ.isLoading && (
            <tr>
              <td colSpan={canManage ? 5 : 4} className="eo-empty">
                {t("pages.orgDetail.loading")}
              </td>
            </tr>
          )}
          {!servicesQ.isLoading && (servicesQ.data ?? []).length === 0 && (
            <tr>
              <td colSpan={canManage ? 5 : 4} className="eo-empty">
                {t("pages.orgDetail.noServices")}
              </td>
            </tr>
          )}
          {(servicesQ.data ?? []).map((s) => (
            <tr
              key={s.id}
              data-active={selected?.id === s.id}
              onClick={() => onSelect(s)}
              style={{ cursor: "pointer" }}
            >
              <td>
                <strong>{s.name}</strong>
              </td>
              <td>
                <code>{s.slug}</code>
              </td>
              <td>{s.description || <span className="eo-mute">—</span>}</td>
              <td>{fmtRel(s.createdAt)}</td>
              {canManage && (
                <td className="eo-col-right">
                  <button
                    type="button"
                    className="eo-btn eo-btn-ghost"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (
                        confirm(
                          tsub("pages.orgDetail.deleteServiceConfirm", {
                            name: s.name,
                          }),
                        )
                      ) {
                        deleteMut.mutate(s.id);
                      }
                    }}
                  >
                    {t("pages.orgDetail.delete")}
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Tokens for the selected service
// ---------------------------------------------------------------------------

function TokensCard({ service }: { service: ServiceRow }) {
  const { t, tsub } = useI18n();
  const auth = useAuth();
  const qc = useQueryClient();
  const tokensQ = useQuery({
    queryKey: ["service-tokens", service.id],
    queryFn: () => fetchServiceTokens(service.id),
  });
  const canManage = auth.isPlatformAdmin || auth.role === "PO";
  const [label, setLabel] = useState("");
  const [issued, setIssued] = useState<IngestTokenCreated | null>(null);
  const createMut = useMutation({
    mutationFn: () => createServiceToken(service.id, label),
    onSuccess: (data) => {
      setIssued(data);
      setLabel("");
      qc.invalidateQueries({ queryKey: ["service-tokens", service.id] });
    },
  });
  const revokeMut = useMutation({
    mutationFn: (id: number) => revokeServiceToken(service.id, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["service-tokens", service.id] }),
  });

  const visible = (tokensQ.data ?? []).filter((t) => !t.revoked);

  return (
    <section className="eo-card">
      <div className="eo-card-h">
        <h3 className="eo-card-title">
          {tsub("pages.orgDetail.tokensFor", { name: service.name })}
        </h3>
        <span className="eo-card-sub">
          {t("pages.orgDetail.tokensEndpointSub")}
        </span>
      </div>

      {canManage && (
        <div className="eo-token-new">
          <input
            className="eo-input"
            type="text"
            placeholder={t("pages.orgDetail.phTokenLabel")}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
          <button
            type="button"
            className="eo-btn eo-btn-primary"
            disabled={createMut.isPending}
            onClick={() => createMut.mutate()}
          >
            {createMut.isPending
              ? t("pages.orgDetail.creating")
              : t("pages.orgDetail.createToken")}
          </button>
        </div>
      )}

      {issued && (
        <div className="eo-token-secret" style={{ marginTop: 8 }}>
          <div style={{ marginBottom: 4 }}>
            {t("pages.orgDetail.saveSecretBanner")}
          </div>
          <code>{issued.secret}</code>
          <div style={{ marginTop: 6, display: "flex", gap: 8 }}>
            <button
              type="button"
              className="eo-btn eo-btn-ghost"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(issued.secret);
                } catch {}
              }}
            >
              {t("pages.orgDetail.copy")}
            </button>
            <button
              type="button"
              className="eo-btn eo-btn-ghost"
              onClick={() => setIssued(null)}
            >
              {t("pages.orgDetail.dismiss")}
            </button>
          </div>
        </div>
      )}

      <table className="eo-table" style={{ marginTop: 10 }}>
        <thead>
          <tr>
            <th>{t("pages.orgDetail.colLabel")}</th>
            <th>{t("pages.orgDetail.colPreview")}</th>
            <th>{t("pages.orgDetail.colCreated")}</th>
            <th>{t("pages.orgDetail.colLastUsed")}</th>
            {canManage && (
              <th className="eo-col-right">{t("pages.orgDetail.colActions")}</th>
            )}
          </tr>
        </thead>
        <tbody>
          {tokensQ.isLoading && (
            <tr>
              <td colSpan={canManage ? 5 : 4} className="eo-empty">
                {t("pages.orgDetail.loading")}
              </td>
            </tr>
          )}
          {!tokensQ.isLoading && visible.length === 0 && (
            <tr>
              <td colSpan={canManage ? 5 : 4} className="eo-empty">
                {t("pages.orgDetail.noActiveTokens")}
              </td>
            </tr>
          )}
          {visible.map((tok) => (
            <tr key={tok.id}>
              <td>{tok.label || <span className="eo-mute">—</span>}</td>
              <td>
                <code>{tok.preview}</code>
              </td>
              <td>{fmtRel(tok.createdAt)}</td>
              <td>
                {tok.lastUsedAt ? (
                  fmtRel(tok.lastUsedAt)
                ) : (
                  <span className="eo-mute">{t("pages.orgDetail.never")}</span>
                )}
              </td>
              {canManage && (
                <td className="eo-col-right">
                  <button
                    type="button"
                    className="eo-btn eo-btn-ghost"
                    onClick={() => {
                      if (
                        confirm(
                          tsub("pages.orgDetail.revokeTokenConfirm", {
                            label: tok.label || tok.preview,
                          }),
                        )
                      ) {
                        revokeMut.mutate(tok.id);
                      }
                    }}
                  >
                    {t("pages.orgDetail.revoke")}
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
