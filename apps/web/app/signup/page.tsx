"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  fetchPublicOrganizations,
  type AuthOrganization,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

/**
 * Sign-up has two faces controlled by ``hasUsers``:
 *
 * - First-ever sign-up: creates the super admin and bootstraps the
 *   ``administrator`` org. No org/role inputs.
 * - Subsequent sign-ups: must pick an existing org and request a role
 *   (PO or DV); the membership is created in ``pending`` status until an
 *   SA/PO approves it from Setup > Organizations.
 */
export default function SignUpPage() {
  const router = useRouter();
  const auth = useAuth();
  const [hasUsers, setHasUsers] = useState<boolean | null>(null);
  const [orgs, setOrgs] = useState<AuthOrganization[]>([]);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [orgId, setOrgId] = useState<string>("");
  const [requestedRole, setRequestedRole] = useState<"PO" | "DV">("DV");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const r = await fetchPublicOrganizations();
        setHasUsers(r.hasUsers);
        setOrgs(r.organizations);
        if (r.organizations.length > 0) setOrgId(r.organizations[0].id);
      } catch {
        setHasUsers(false);
      }
    })();
  }, []);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const me = await auth.signUp({
        email,
        password,
        displayName,
        orgId: hasUsers ? orgId : undefined,
        requestedRole: hasUsers ? requestedRole : undefined,
      });
      if (me.user.isSuperAdmin) {
        router.replace("/workspace/");
      } else if (me.approvedMemberships.length > 0) {
        router.replace("/workspace/");
      } else {
        router.replace("/pending");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign-up failed");
    } finally {
      setBusy(false);
    }
  };

  if (hasUsers === null) {
    return (
      <div className="eo-auth-wrap">
        <div className="eo-auth-card">
          <p className="eo-mute">Loading…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="eo-auth-wrap">
      <div className="eo-auth-card">
        <div className="eo-auth-brand">EasyObs</div>
        <h1 className="eo-auth-title">
          {hasUsers ? "Create account" : "Create the first super admin"}
        </h1>
        <p className="eo-auth-lede">
          {hasUsers ? (
            <>
              The first administrator who approves your request will grant
              access. Already have an account?{" "}
              <Link href="/signin">Sign in</Link>.
            </>
          ) : (
            "No users exist yet. The first sign-up becomes the super admin and the default 'administrator' organization is created automatically."
          )}
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
            <span>Display name</span>
            <input
              className="eo-input"
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              required
            />
          </label>
          <label className="eo-auth-field">
            <span>Password (min 8 chars)</span>
            <input
              className="eo-input"
              type="password"
              autoComplete="new-password"
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          {hasUsers && (
            <>
              <label className="eo-auth-field">
                <span>Organization</span>
                <select
                  className="eo-input"
                  value={orgId}
                  onChange={(e) => setOrgId(e.target.value)}
                  required
                >
                  {orgs.length === 0 && <option value="">(none)</option>}
                  {orgs.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.name}
                      {o.isDefault ? "  (default)" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label className="eo-auth-field">
                <span>Requested role</span>
                <select
                  className="eo-input"
                  value={requestedRole}
                  onChange={(e) =>
                    setRequestedRole(e.target.value as "PO" | "DV")
                  }
                >
                  <option value="DV">DV — Developer (read assigned services)</option>
                  <option value="PO">PO — Project owner (manage org)</option>
                </select>
              </label>
            </>
          )}
          {error && <div className="eo-auth-error">{error}</div>}
          <button
            type="submit"
            className="eo-btn eo-btn-primary"
            disabled={busy || (hasUsers && !orgId)}
          >
            {busy
              ? "Creating…"
              : hasUsers
                ? "Request access"
                : "Create super admin"}
          </button>
        </form>
      </div>
    </div>
  );
}
