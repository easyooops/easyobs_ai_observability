"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAuth, type AuthState } from "@/lib/auth";
import { useWorkspace, type WindowChoice } from "@/lib/context";
import { useI18n, type AppLocale } from "@/lib/i18n/context";

type NavItem = {
  href: string;
  i18nKey: string;
  icon: React.ReactNode;
  badge?: string;
  /** Roles allowed; ``undefined`` means everyone authenticated. */
  allow?: ("SA" | "PO" | "DV")[];
  /** When true the item is only visible to platform admins (SA or any
   * approved PO of the ``administrator`` org). Overrides ``allow``. */
  platformAdminOnly?: boolean;
};

function Icon({ d, viewBox = "0 0 20 20" }: { d: string; viewBox?: string }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox={viewBox}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={d} />
    </svg>
  );
}

const NAV_MAIN: NavItem[] = [
  {
    href: "/workspace/",
    i18nKey: "nav.overview",
    icon: <Icon d="M3 12l2-2 3 3 5-5 4 4M3 17h14" />,
  },
  {
    href: "/workspace/tracing/",
    i18nKey: "nav.tracing",
    icon: <Icon d="M4 5h12M4 10h8M4 15h10M14 15a2 2 0 104 0 2 2 0 00-4 0zM10 10a2 2 0 104 0 2 2 0 00-4 0zM14 5a2 2 0 104 0 2 2 0 00-4 0z" />,
  },
  {
    href: "/workspace/interactions/",
    i18nKey: "nav.interactions",
    icon: <Icon d="M6 8a4 4 0 118 0 4 4 0 01-8 0zM2 18a8 8 0 0116 0" />,
  },
];

const NAV_QUALITY: NavItem[] = [
  {
    href: "/workspace/quality/profiles/",
    i18nKey: "nav.profiles",
    icon: <Icon d="M4 6h12M4 10h12M4 14h8" />,
  },
  {
    href: "/workspace/quality/runs/",
    i18nKey: "nav.runs",
    icon: <Icon d="M5 4l11 6-11 6V4z" />,
  },
  {
    href: "/workspace/quality/improvements/",
    i18nKey: "nav.improvements",
    icon: <Icon d="M3 14l4-4 3 3 7-7M14 6h4v4" />,
  },
  {
    href: "/workspace/quality/golden/",
    i18nKey: "nav.goldenSets",
    icon: <Icon d="M10 2l2.4 5.4 5.6.6-4.2 3.8 1.2 5.8L10 14.8 5 17.6l1.2-5.8L2 8l5.6-.6L10 2z" />,
  },
  {
    href: "/workspace/quality/judges/",
    i18nKey: "nav.judges",
    icon: <Icon d="M3 17l5-12 4 9 3-5 2 8H3z" />,
  },
];

const NAV_SETUP: NavItem[] = [
  {
    href: "/workspace/sdk/",
    i18nKey: "nav.sdk",
    icon: <Icon d="M9 11a4 4 0 10-4-4M14 11l6 6M14 11l3-3M18 14l3-3" />,
  },
  {
    href: "/workspace/setup/organizations/",
    i18nKey: "nav.organizations",
    icon: (
      <Icon d="M3 17v-1a3 3 0 013-3h3a3 3 0 013 3v1M11 17v-1a3 3 0 013-3h2a3 3 0 013 3v1M7 9a2 2 0 100-4 2 2 0 000 4zM15 9a2 2 0 100-4 2 2 0 000 4z" />
    ),
    allow: ["SA", "PO"],
  },
  {
    href: "/workspace/settings/",
    i18nKey: "nav.settings",
    icon: <Icon d="M10 3v2M10 15v2M4.2 4.2l1.4 1.4M14.4 14.4l1.4 1.4M3 10h2M15 10h2M4.2 15.8l1.4-1.4M14.4 5.6l1.4-1.4M10 7a3 3 0 100 6 3 3 0 000-6z" />,
    platformAdminOnly: true,
  },
];

const WINDOW_OPTIONS: Exclude<WindowChoice, "custom">[] = ["1h", "6h", "24h", "7d"];

// The `<input type="datetime-local">` widget speaks **wall-clock time in the
// user's local timezone**, but the rest of EasyObs (server filters, chart
// axes, ingest timestamps) is in UTC. We deliberately treat the picker's
// value as UTC instead -- isoToLocalInput renders an ISO into UTC clock
// components, and localInputToUtcIso parses the picker string back into a
// UTC instant. The picker labels say "(UTC)" so users see what's expected.
function isoToLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}` +
    `T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
  );
}

function localInputToUtcIso(local: string): string | null {
  const m = local.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/);
  if (!m) return null;
  const [, y, mo, d, h, mi, s] = m;
  const ts = Date.UTC(
    Number(y),
    Number(mo) - 1,
    Number(d),
    Number(h),
    Number(mi),
    Number(s ?? 0),
  );
  return new Date(ts).toISOString();
}

function rangeLabel(fromIso: string, toIso: string): string {
  try {
    const f = new Date(fromIso);
    const t = new Date(toIso);
    const opts: Intl.DateTimeFormatOptions = {
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "UTC",
    };
    const fmt = new Intl.DateTimeFormat("en-US", opts);
    return `${fmt.format(f)} → ${fmt.format(t)} UTC`;
  } catch {
    return "custom";
  }
}

function CustomRangePicker() {
  const { customFrom, customTo, setCustomRange, window: w } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [fromLocal, setFromLocal] = useState("");
  const [toLocal, setToLocal] = useState("");
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const isActive = w === "custom" && !!customFrom && !!customTo;

  useEffect(() => {
    if (!open) return;
    if (customFrom && customTo) {
      setFromLocal(isoToLocalInput(customFrom));
      setToLocal(isoToLocalInput(customTo));
    } else {
      const now = new Date();
      const past = new Date(now.getTime() - 24 * 3600 * 1000);
      setFromLocal(isoToLocalInput(past.toISOString()));
      setToLocal(isoToLocalInput(now.toISOString()));
    }
    setError(null);
  }, [open, customFrom, customTo]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const apply = () => {
    if (!fromLocal || !toLocal) {
      setError("Pick both From and To.");
      return;
    }
    const fromIso = localInputToUtcIso(fromLocal);
    const toIso = localInputToUtcIso(toLocal);
    if (!fromIso || !toIso) {
      setError("Could not parse the picker value.");
      return;
    }
    if (new Date(fromIso).getTime() >= new Date(toIso).getTime()) {
      setError("From must be earlier than To.");
      return;
    }
    setCustomRange(fromIso, toIso);
    setOpen(false);
  };

  const quick = (hours: number) => {
    const now = new Date();
    const past = new Date(now.getTime() - hours * 3600 * 1000);
    setFromLocal(isoToLocalInput(past.toISOString()));
    setToLocal(isoToLocalInput(now.toISOString()));
    setError(null);
  };

  return (
    <div className="eo-range" ref={rootRef}>
      <button
        type="button"
        className="eo-chip eo-range-chip"
        data-active={isActive}
        onClick={() => setOpen((v) => !v)}
        title={isActive ? `${customFrom} → ${customTo}` : "Custom range"}
      >
        <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8">
          <rect x="3" y="5" width="14" height="12" rx="2" />
          <path d="M3 9h14M7 3v4M13 3v4" strokeLinecap="round" />
        </svg>
        {isActive && customFrom && customTo ? rangeLabel(customFrom, customTo) : "Custom"}
      </button>
      {open && (
        <div className="eo-range-pop" role="dialog" aria-label="Custom range">
          <div className="eo-range-row">
            <label>
              <span>From (UTC)</span>
              <input
                type="datetime-local"
                value={fromLocal}
                onChange={(e) => setFromLocal(e.target.value)}
              />
            </label>
            <label>
              <span>To (UTC)</span>
              <input
                type="datetime-local"
                value={toLocal}
                onChange={(e) => setToLocal(e.target.value)}
              />
            </label>
          </div>
          <div className="eo-range-quick">
            {([
              ["Last 1h", 1],
              ["Last 6h", 6],
              ["Last 24h", 24],
              ["Last 7d", 168],
            ] as const).map(([label, h]) => (
              <button key={label} type="button" className="eo-btn eo-btn-ghost" onClick={() => quick(h)}>
                {label}
              </button>
            ))}
          </div>
          {error && <div className="eo-range-error">{error}</div>}
          <div className="eo-range-actions">
            <button type="button" className="eo-btn eo-btn-ghost" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button type="button" className="eo-btn eo-btn-primary" onClick={apply}>
              Apply
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function visibleNav(
  items: NavItem[],
  role: "SA" | "PO" | "DV" | null,
  isPlatformAdmin: boolean,
): NavItem[] {
  return items.filter((item) => {
    if (item.platformAdminOnly) return isPlatformAdmin;
    if (!item.allow) return true;
    if (!role) return false;
    return item.allow.includes(role);
  });
}

function isAllowedPath(
  pathname: string,
  role: "SA" | "PO" | "DV" | null,
  isPlatformAdmin: boolean,
): boolean {
  // Pages that require explicit role gating; everything else under
  // /workspace/ is considered open to any approved member.
  if (pathname.startsWith("/workspace/settings")) return isPlatformAdmin;
  if (pathname.startsWith("/workspace/setup/organizations")) {
    // Org management is open to platform admin (SA + admin/PO) and to
    // any PO of the currently-selected org.
    return isPlatformAdmin || role === "PO";
  }
  return true;
}

function navItemMatchesPath(pathname: string, href: string): boolean {
  if (href === "/workspace/") {
    return pathname === "/workspace" || pathname === "/workspace/";
  }
  const p = pathname.replace(/\/$/, "") || "/";
  const h = href.replace(/\/$/, "") || "/";
  if (p === h) return true;
  return p.startsWith(h + "/");
}

function activeNavLabel(
  pathname: string,
  items: NavItem[],
  t: (k: string) => string,
): string {
  const matched = items.filter((n) => navItemMatchesPath(pathname, n.href));
  if (matched.length === 0) return t("nav.overview");
  return t(
    matched.reduce((a, b) => (a.href.length >= b.href.length ? a : b)).i18nKey,
  );
}

/** Only the most specific matching route is active (fixes Quality Overview + Runs + Improvements all lit). */
function isNavItemActive(pathname: string, item: NavItem, allItems: NavItem[]): boolean {
  const candidates = allItems.filter((n) => navItemMatchesPath(pathname, n.href));
  if (candidates.length === 0) return false;
  const best = candidates.reduce((a, b) => (a.href.length >= b.href.length ? a : b));
  const ih = item.href.replace(/\/$/, "") || "/";
  const bh = best.href.replace(/\/$/, "") || "/";
  return ih === bh;
}

function userInitials(s: string): string {
  const trimmed = (s || "").trim();
  if (!trimmed) return "EO";
  const parts = trimmed.split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function useAuthGuard(auth: AuthState, pathname: string, role: AuthState["role"]) {
  const router = useRouter();
  useEffect(() => {
    if (auth.status === "loading") return;
    if (auth.status === "anonymous") {
      router.replace("/signin");
      return;
    }
    const isSA = auth.user?.isSuperAdmin ?? false;
    // Platform admins (SA + admin/PO) and platform members (admin/DV) are
    // always considered "approved" even before they pick an org.
    const elevated = isSA || auth.isPlatformAdmin || auth.isPlatformMember;
    if (!elevated && auth.approvedMemberships.length === 0) {
      router.replace("/pending");
      return;
    }
    if (!auth.currentOrg) {
      router.replace("/signin?step=org");
      return;
    }
    if (!isAllowedPath(pathname, role, auth.isPlatformAdmin)) {
      router.replace("/workspace/");
    }
  }, [
    auth.status,
    auth.user,
    auth.approvedMemberships,
    auth.currentOrg,
    auth.isPlatformAdmin,
    auth.isPlatformMember,
    pathname,
    role,
    router,
  ]);
}

function LanguageSwitcher() {
  const { locale, setLocale, t } = useI18n();
  return (
    <label className="eo-chip eo-lang-switcher" style={{ display: "flex", alignItems: "center", gap: 6, padding: "2px 8px" }}>
      <span className="eo-mute eo-lang-label" style={{ fontSize: 11 }}>
        {t("language.ui")}
      </span>
      <select
        value={locale}
        onChange={(e) => setLocale(e.target.value as AppLocale)}
        aria-label={t("language.ui")}
        style={{
          fontSize: 12,
          border: "none",
          background: "transparent",
          color: "inherit",
          cursor: "pointer",
        }}
      >
        <option value="en">{t("language.en")}</option>
        <option value="ko">{t("language.ko")}</option>
      </select>
    </label>
  );
}

export function WorkspaceShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const auth = useAuth();
  const { locale, t } = useI18n();
  const docsLang = locale === "ko" ? "kr" : "en";
  const [collapsed, setCollapsed] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [orgPickerOpen, setOrgPickerOpen] = useState(false);
  const orgPickerRef = useRef<HTMLDivElement | null>(null);
  const {
    window: timeWindow,
    setWindow,
    live,
    setLive,
    search,
    setSearch,
  } = useWorkspace();

  useAuthGuard(auth, pathname, auth.role);

  useEffect(() => {
    setMobileNavOpen(false);
  }, [pathname]);

  const navMain = useMemo(() => NAV_MAIN, []);
  const navQuality = useMemo(() => NAV_QUALITY, []);
  const navSetup = useMemo(
    () => visibleNav(NAV_SETUP, auth.role, auth.isPlatformAdmin),
    [auth.role, auth.isPlatformAdmin],
  );

  const allNavItems = useMemo(
    () => [...NAV_MAIN, ...NAV_QUALITY, ...navSetup],
    [navSetup],
  );

  const activeLabel = activeNavLabel(pathname, allNavItems, t);

  const renderLink = (item: NavItem) => {
    const active = isNavItemActive(pathname, item, allNavItems);
    const label = t(item.i18nKey);
    return (
      <Link
        key={item.href}
        href={item.href}
        className="eo-nav-link"
        data-active={active}
        title={label}
      >
        <span className="eo-nav-icon">{item.icon}</span>
        <span>{label}</span>
        {item.badge && <span className="eo-nav-badge">{item.badge}</span>}
      </Link>
    );
  };

  useEffect(() => {
    if (!orgPickerOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!orgPickerRef.current?.contains(e.target as Node)) setOrgPickerOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [orgPickerOpen]);

  if (auth.status !== "authenticated" || !auth.currentOrg) {
    return (
      <div className="eo-shell" style={{ display: "grid", placeItems: "center" }}>
        <p className="eo-mute">Loading…</p>
      </div>
    );
  }

  // The org picker is meant for accounts that can move across orgs:
  // bootstrapped SA, admin/PO (platform admin), and admin/DV (platform
  // member, read-only across orgs).
  const canSwitchOrg = auth.isPlatformAdmin || auth.isPlatformMember;
  const orgName = auth.currentOrg.name;
  const userLabel = auth.user?.displayName || auth.user?.email || "user";

  return (
    <div className="eo-shell" data-collapsed={collapsed}>
      <aside className="eo-rail" aria-label="main navigation">
        <div className="eo-rail-head">
          <span className="eo-brand-mark" aria-hidden />
          <div>
            <div className="eo-brand">EasyObs</div>
            <div className="eo-brand-sub">signals console</div>
          </div>
        </div>

        <div className="eo-rail-scope">
          <div className="eo-scope-row">
            <span>Organization</span>
            <strong title={orgName}>{orgName}</strong>
          </div>
          <div className="eo-scope-row">
            <span>Role</span>
            <span className="eo-pill-role" data-role={auth.role ?? "DV"}>
              {auth.role ?? "—"}
            </span>
          </div>
        </div>

        <nav className="eo-rail-nav">
          <div className="eo-rail-group">{t("nav.groupObserve")}</div>
          {navMain.map(renderLink)}
          <div className="eo-rail-group">{t("nav.groupQuality")}</div>
          {navQuality.map(renderLink)}
          <div className="eo-rail-group">{t("nav.groupSetup")}</div>
          {navSetup.map(renderLink)}
          <div className="eo-rail-group">DOCS</div>
          <a
            href={`/docs/${docsLang}/index.html`}
            target="_blank"
            rel="noopener noreferrer"
            className="eo-nav-link"
            title="Documentation"
          >
            <span className="eo-nav-icon"><Icon d="M4 3h12a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2zM7 7h6M7 10h6M7 13h4" /></span>
            <span>Docs</span>
          </a>
        </nav>

        <div className="eo-rail-foot">
          <div className="eo-avatar">{userInitials(userLabel)}</div>
          <span title={auth.user?.email}>{userLabel}</span>
          <button
            type="button"
            className="eo-btn"
            style={{ marginLeft: "auto", padding: "2px 6px", fontSize: 10 }}
            onClick={() => auth.signOut()}
            title="Sign out"
          >
            ⎋
          </button>
          <button
            type="button"
            className="eo-btn"
            style={{ padding: "2px 6px", fontSize: 10 }}
            onClick={() => setCollapsed((c) => !c)}
            title="Toggle rail"
          >
            {collapsed ? "›" : "‹"}
          </button>
        </div>
      </aside>

      {mobileNavOpen && (
        <div className="eo-mobile-overlay" onClick={() => setMobileNavOpen(false)}>
          <aside className="eo-mobile-nav" onClick={(e) => e.stopPropagation()} aria-label="mobile navigation">
            <div className="eo-rail-head">
              <span className="eo-brand-mark" aria-hidden />
              <div>
                <div className="eo-brand">EasyObs</div>
                <div className="eo-brand-sub">signals console</div>
              </div>
              <button
                type="button"
                className="eo-mobile-close"
                onClick={() => setMobileNavOpen(false)}
                aria-label="Close menu"
              >
                ✕
              </button>
            </div>

            <div className="eo-rail-scope">
              <div className="eo-scope-row">
                <span>Organization</span>
                <strong title={orgName}>{orgName}</strong>
              </div>
              <div className="eo-scope-row">
                <span>Role</span>
                <span className="eo-pill-role" data-role={auth.role ?? "DV"}>
                  {auth.role ?? "—"}
                </span>
              </div>
            </div>

            <nav className="eo-rail-nav">
              <div className="eo-rail-group">{t("nav.groupObserve")}</div>
              {navMain.map(renderLink)}
              <div className="eo-rail-group">{t("nav.groupQuality")}</div>
              {navQuality.map(renderLink)}
              <div className="eo-rail-group">{t("nav.groupSetup")}</div>
              {navSetup.map(renderLink)}
              <div className="eo-rail-group">DOCS</div>
              <a
                href={`/docs/${docsLang}/index.html`}
                target="_blank"
                rel="noopener noreferrer"
                className="eo-nav-link"
                title="Documentation"
              >
                <span className="eo-nav-icon"><Icon d="M4 3h12a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2zM7 7h6M7 10h6M7 13h4" /></span>
                <span>Docs</span>
              </a>
            </nav>

            <div className="eo-rail-foot">
              <div className="eo-avatar">{userInitials(userLabel)}</div>
              <span title={auth.user?.email}>{userLabel}</span>
              <button
                type="button"
                className="eo-btn"
                style={{ marginLeft: "auto", padding: "2px 6px", fontSize: 10 }}
                onClick={() => auth.signOut()}
                title="Sign out"
              >
                ⎋
              </button>
            </div>
          </aside>
        </div>
      )}

      <div className="eo-main">
        <div className="eo-topbar">
          <button
            type="button"
            className="eo-mobile-menu-btn"
            onClick={() => setMobileNavOpen(true)}
            aria-label="Open menu"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
              <path d="M3 5h14M3 10h14M3 15h14" />
            </svg>
          </button>
          <div className="eo-topbar-crumbs">
            <span>organization</span>
            <span>/</span>
            <div className="eo-range" ref={orgPickerRef} style={{ position: "relative" }}>
              <button
                type="button"
                className="eo-chip"
                data-active={orgPickerOpen}
                onClick={() => canSwitchOrg && setOrgPickerOpen((v) => !v)}
                style={{ cursor: canSwitchOrg ? "pointer" : "default" }}
                title={canSwitchOrg ? "Switch organization" : orgName}
              >
                <strong>{orgName}</strong>
                {canSwitchOrg && <span style={{ marginLeft: 4, fontSize: 10 }}>▾</span>}
              </button>
              {orgPickerOpen && canSwitchOrg && (
                <OrgPicker close={() => setOrgPickerOpen(false)} />
              )}
            </div>
            <span>/</span>
            <strong>{activeLabel}</strong>
          </div>

          <div className="eo-topbar-spacer" />

          <div className="eo-topbar-tools">
            <LanguageSwitcher />
            <div className="eo-search">
              <svg width="13" height="13" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8">
                <circle cx="9" cy="9" r="6" />
                <path d="M14 14l4 4" strokeLinecap="round" />
              </svg>
              <input
                placeholder={t("topbar.searchPlaceholder")}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <span className="eo-kbd">/</span>
            </div>

            <div className="eo-seg" aria-label="time window">
              {WINDOW_OPTIONS.map((w) => (
                <button
                  key={w}
                  type="button"
                  onClick={() => setWindow(w)}
                  data-active={timeWindow === w}
                >
                  {w}
                </button>
              ))}
            </div>

            <CustomRangePicker />

            <button
              type="button"
              className="eo-chip"
              data-active={live}
              onClick={() => setLive(!live)}
              title={t("topbar.autoRefresh")}
            >
              <span className="eo-chip-dot" style={{ background: live ? "var(--eo-ok)" : "var(--eo-mute)" }} />
              {live ? t("topbar.live") : t("topbar.paused")}
            </button>

            <button
              type="button"
              className="eo-btn eo-btn-ghost"
              onClick={() => router.push("/workspace/")}
              title={`Signed in as ${auth.user?.email ?? ""}`}
              style={{ display: "none" }}
            >
              {userLabel}
            </button>
          </div>
        </div>

        <div className="eo-page">{children}</div>
      </div>
    </div>
  );
}

function OrgPicker({ close }: { close: () => void }) {
  const auth = useAuth();
  const [orgs, setOrgs] = useState<{ id: string; name: string }[] | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const { fetchOrganizations } = await import("@/lib/api");
        const list = await fetchOrganizations();
        setOrgs(list.map((o) => ({ id: o.id, name: o.name })));
      } catch {
        setOrgs([]);
      }
    })();
  }, []);

  const pick = async (orgId: string) => {
    if (busy || orgId === auth.currentOrg?.id) {
      close();
      return;
    }
    setBusy(true);
    try {
      await auth.selectOrganization(orgId);
      close();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="eo-range-pop"
      role="dialog"
      aria-label="Switch organization"
      style={{ minWidth: 200, top: "calc(100% + 6px)" }}
    >
      <div className="eo-range-row" style={{ flexDirection: "column", gap: 4 }}>
        {orgs == null && <span className="eo-mute">loading…</span>}
        {orgs?.length === 0 && <span className="eo-mute">no organizations</span>}
        {(orgs ?? []).map((o) => (
          <button
            key={o.id}
            type="button"
            className="eo-btn eo-btn-ghost"
            data-active={o.id === auth.currentOrg?.id}
            onClick={() => pick(o.id)}
            disabled={busy}
            style={{ justifyContent: "flex-start", textAlign: "left" }}
          >
            {o.name}
            {o.id === auth.currentOrg?.id ? "  ✓" : ""}
          </button>
        ))}
      </div>
    </div>
  );
}
