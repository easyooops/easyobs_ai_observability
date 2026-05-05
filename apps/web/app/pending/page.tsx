"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  fetchPublicOrganizations,
  requestAccess,
  type AuthOrganization,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function PendingPage() {
  const router = useRouter();
  const auth = useAuth();
  const [orgs, setOrgs] = useState<AuthOrganization[]>([]);
  const [orgId, setOrgId] = useState("");
  const [role, setRole] = useState<"PO" | "DV">("DV");
  const [busy, setBusy] = useState(false);
  const [info, setInfo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (auth.status === "anonymous") {
      router.replace("/signin");
      return;
    }
    if (auth.status === "authenticated") {
      // Anyone with the SA flag, an approved org membership, or
      // platform-admin/member status from administrator org goes
      // straight into the workspace.
      if (
        auth.user?.isSuperAdmin ||
        auth.isPlatformAdmin ||
        auth.isPlatformMember ||
        auth.approvedMemberships.length > 0
      ) {
        router.replace("/workspace/");
      }
    }
  }, [auth, router]);

  // Poll /v1/auth/me every 5s while the user is sitting on this page so
  // an approval landing on the backend lets them in automatically — no
  // manual refresh needed. Stop polling on unmount or when the page
  // becomes hidden to be a good tab citizen.
  useEffect(() => {
    if (auth.status !== "authenticated") return;
    let cancelled = false;
    const tick = () => {
      if (cancelled || document.visibilityState === "hidden") return;
      auth.refresh().catch(() => undefined);
    };
    const id = window.setInterval(tick, 5000);
    const onVisible = () => {
      if (document.visibilityState === "visible") tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [auth.status, auth.refresh]);

  useEffect(() => {
    void (async () => {
      try {
        const r = await fetchPublicOrganizations();
        const myOrgIds = new Set([
          ...auth.approvedMemberships.map((m) => m.orgId),
          ...auth.pendingMemberships.map((m) => m.orgId),
        ]);
        const list = r.organizations.filter((o) => !myOrgIds.has(o.id));
        setOrgs(list);
        if (list.length > 0) setOrgId(list[0].id);
      } catch {
        setOrgs([]);
      }
    })();
  }, [auth.approvedMemberships, auth.pendingMemberships]);

  if (auth.status !== "authenticated" || !auth.user) {
    return (
      <div className="eo-auth-wrap">
        <div className="eo-auth-card">
          <p className="eo-mute">Loading…</p>
        </div>
      </div>
    );
  }

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!orgId) return;
    setError(null);
    setInfo(null);
    setBusy(true);
    try {
      await requestAccess(orgId, role);
      setInfo("Request submitted. An admin will be in touch.");
      await auth.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="eo-auth-wrap">
      <div className="eo-auth-card">
        <div className="eo-auth-brand">EasyObs</div>
        <h1 className="eo-auth-title">Awaiting approval</h1>
        <p className="eo-auth-lede">
          Hi {auth.user.displayName || auth.user.email}, your account has no
          approved organization memberships yet. An admin needs to review one
          of your pending requests. This page auto-refreshes every 5
          seconds — once approved you will be taken into the workspace
          automatically.
        </p>

        {auth.pendingMemberships.length > 0 && (
          <div className="eo-auth-pending">
            <div className="eo-auth-pending-h">Pending requests</div>
            <ul>
              {auth.pendingMemberships.map((m) => (
                <li key={m.orgId}>
                  <strong>{m.orgName}</strong>
                  {" — "}
                  <span className="eo-mute">requested role {m.role}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <form onSubmit={onSubmit} className="eo-auth-form">
          <h2 className="eo-auth-subtitle">Request additional access</h2>
          <label className="eo-auth-field">
            <span>Organization</span>
            <select
              className="eo-input"
              value={orgId}
              onChange={(e) => setOrgId(e.target.value)}
              disabled={orgs.length === 0}
            >
              {orgs.length === 0 && <option value="">(no other orgs)</option>}
              {orgs.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </label>
          <label className="eo-auth-field">
            <span>Requested role</span>
            <select
              className="eo-input"
              value={role}
              onChange={(e) => setRole(e.target.value as "PO" | "DV")}
            >
              <option value="DV">DV — Developer</option>
              <option value="PO">PO — Project owner</option>
            </select>
          </label>
          {info && <div className="eo-auth-info">{info}</div>}
          {error && <div className="eo-auth-error">{error}</div>}
          <div style={{ display: "flex", gap: 8, justifyContent: "space-between" }}>
            <button
              type="button"
              className="eo-btn eo-btn-ghost"
              onClick={() => auth.signOut()}
            >
              Sign out
            </button>
            <button
              type="submit"
              className="eo-btn eo-btn-primary"
              disabled={busy || !orgId}
            >
              {busy ? "Submitting…" : "Request access"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
