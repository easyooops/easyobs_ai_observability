"use client";

/**
 * Client-side gate for the Quality (evaluation) surface.
 *
 * The router itself enforces the (org × service) access matrix server-side
 * — every endpoint walks through ``CallerScope`` before it touches the
 * service layer, and the lifespan only mounts the routes when
 * ``settings.eval_enabled`` is true. This guard is purely a UX nicety:
 *
 *  - hide the surface from anonymous / pending users (the workspace shell
 *    already routes them away, but we still defend in case this component
 *    is mounted directly), and
 *  - render a friendly fallback instead of an empty page when the API
 *    returns 503 (the eval module is disabled in this deployment).
 *
 * Mutating actions (create/edit/delete/run) are gated separately by
 * ``canMutateQuality`` so platform-member (admin/DV) accounts get a
 * read-only view across orgs.
 */

import Link from "next/link";
import { useAuth, type AuthState } from "@/lib/auth";
import { useQuery } from "@tanstack/react-query";
import { fetchQualityOverview } from "@/lib/api";

export function canMutateQuality(auth: AuthState): boolean {
  if (auth.status !== "authenticated") return false;
  if (auth.user?.isSuperAdmin) return true;
  if (auth.isPlatformAdmin) return true;
  if (auth.isPlatformMember) return false;
  return auth.role === "PO";
}

export function QualityGuard({
  children,
}: {
  children: React.ReactNode;
}) {
  const auth = useAuth();
  const ping = useQuery({
    queryKey: ["quality", "ping"],
    queryFn: fetchQualityOverview,
    retry: false,
    staleTime: 60_000,
  });

  if (auth.status === "loading") {
    return <div className="eo-empty">Loading…</div>;
  }
  if (auth.status === "anonymous" || !auth.currentOrg) {
    return (
      <div className="eo-empty">
        Sign in and pick an organization to use the Quality module.
      </div>
    );
  }
  if (ping.isError) {
    return (
      <div className="eo-empty">
        The Quality (evaluation) module isn't enabled on this server. Set
        <code style={{ margin: "0 4px" }}>EASYOBS_EVAL_ENABLED=true</code>
        and restart the API to activate it. <br />
        Until then the rest of EasyObs continues to operate normally —{" "}
        <Link href="/workspace/" className="eo-link">
          back to overview
        </Link>
        .
      </div>
    );
  }
  return <>{children}</>;
}

export function ScopeBanner() {
  const auth = useAuth();
  const services = auth.accessibleServiceIds;
  return (
    <div className="eo-page-meta" style={{ marginBottom: 8 }}>
      <span className="eo-tag">org: {auth.currentOrg?.name ?? "—"}</span>
      <span className="eo-tag">role: {auth.role ?? "—"}</span>
      <span className="eo-tag eo-tag-accent">
        services: {services == null ? "all" : services.length}
      </span>
    </div>
  );
}

export function WriteHint() {
  const auth = useAuth();
  if (canMutateQuality(auth)) return null;
  return (
    <div className="eo-empty" style={{ padding: 8 }}>
      Read-only view — your role can't mutate evaluation state in this org.
    </div>
  );
}
