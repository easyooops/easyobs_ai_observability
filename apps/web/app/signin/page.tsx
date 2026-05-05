"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

/**
 * Sign-in is a two-step flow:
 *
 * 1. ``credentials`` — email + password. Successful auth either lands the
 *    user directly on /workspace (single approved org) or advances to step 2.
 * 2. ``select-org`` — pick which approved org becomes the active context.
 *    SAs always see the picker (so they can switch tenants intentionally).
 */
export default function SignInPage() {
  return (
    <Suspense fallback={<div className="eo-auth-wrap" />}>
      <SignInInner />
    </Suspense>
  );
}

function SignInInner() {
  const router = useRouter();
  const params = useSearchParams();
  const auth = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState<"credentials" | "select-org">(
    params.get("step") === "org" ? "select-org" : "credentials",
  );

  useEffect(() => {
    if (auth.status !== "authenticated") return;
    const memberships = auth.approvedMemberships;
    const elevated =
      auth.user?.isSuperAdmin || auth.isPlatformAdmin || auth.isPlatformMember;
    if (elevated || memberships.length > 1) {
      setStep("select-org");
      return;
    }
    if (memberships.length === 0) {
      router.replace("/pending");
      return;
    }
    if (auth.currentOrg) {
      router.replace("/workspace/");
    }
  }, [auth, router]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const me = await auth.signIn(email, password);
      const elevated =
        me.user.isSuperAdmin ||
        (me.isPlatformAdmin ?? false) ||
        (me.isPlatformMember ?? false);
      if (elevated || me.approvedMemberships.length > 1) {
        setStep("select-org");
      } else if (me.approvedMemberships.length === 0) {
        router.replace("/pending");
      } else {
        router.replace("/workspace/");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  };

  if (step === "select-org" && auth.status === "authenticated") {
    return <SelectOrgStep />;
  }

  return (
    <div className="eo-auth-wrap">
      <div className="eo-auth-card">
        <div className="eo-auth-brand">EasyObs</div>
        <h1 className="eo-auth-title">Sign in</h1>
        <p className="eo-auth-lede">
          Use the email and password you registered with. New here?{" "}
          <Link href="/signup">Create an account</Link>.
        </p>
        <form onSubmit={onSubmit} className="eo-auth-form">
          <label className="eo-auth-field">
            <span>Email</span>
            <input
              className="eo-input"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </label>
          <label className="eo-auth-field">
            <span>Password</span>
            <input
              className="eo-input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          {error && <div className="eo-auth-error">{error}</div>}
          <button
            type="submit"
            className="eo-btn eo-btn-primary"
            disabled={busy}
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}

function SelectOrgStep() {
  const router = useRouter();
  const auth = useAuth();
  const memberships = auth.approvedMemberships;
  // SA, admin/PO, and admin/DV can all hop into any org from this picker
  // (admin/DV gets read-only access). For everyone else we restrict to
  // their explicit approved memberships.
  const canPickAnyOrg = auth.isPlatformMember;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [orgId, setOrgId] = useState<string>(
    auth.currentOrg?.id ?? memberships[0]?.orgId ?? "",
  );

  const [allOrgs, setAllOrgs] = useState<
    { id: string; name: string }[] | null
  >(null);
  useEffect(() => {
    if (!canPickAnyOrg) return;
    void (async () => {
      try {
        const { fetchOrganizations } = await import("@/lib/api");
        const list = await fetchOrganizations();
        setAllOrgs(list.map((o) => ({ id: o.id, name: o.name })));
        if (!orgId && list.length > 0) setOrgId(list[0].id);
      } catch {
        setAllOrgs([]);
      }
    })();
    // orgId intentionally omitted: only seed once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canPickAnyOrg]);

  const orgs = canPickAnyOrg
    ? allOrgs ?? []
    : memberships.map((m) => ({ id: m.orgId, name: m.orgName }));

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!orgId) return;
    setError(null);
    setBusy(true);
    try {
      await auth.selectOrganization(orgId);
      router.replace("/workspace/");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to select organization");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="eo-auth-wrap">
      <div className="eo-auth-card">
        <div className="eo-auth-brand">EasyObs</div>
        <h1 className="eo-auth-title">Select organization</h1>
        <p className="eo-auth-lede">
          {canPickAnyOrg
            ? "You can enter any organization. Pick which one to start in — you can switch later from the top bar."
            : "Pick the organization you want to work in. You can switch later from the top bar."}
        </p>
        <form onSubmit={onSubmit} className="eo-auth-form">
          <label className="eo-auth-field">
            <span>Organization</span>
            <select
              className="eo-input"
              value={orgId}
              onChange={(e) => setOrgId(e.target.value)}
            >
              {orgs.length === 0 && <option value="">(none available)</option>}
              {orgs.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </label>
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
              {busy ? "Entering…" : "Enter workspace"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
