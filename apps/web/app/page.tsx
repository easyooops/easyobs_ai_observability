"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "@/lib/auth";

export default function Home() {
  const router = useRouter();
  const auth = useAuth();

  useEffect(() => {
    if (auth.status === "loading") return;
    if (auth.status === "anonymous") {
      router.replace("/signin");
      return;
    }
    const elevated =
      auth.user?.isSuperAdmin || auth.isPlatformAdmin || auth.isPlatformMember;
    if (!elevated && auth.approvedMemberships.length === 0) {
      router.replace("/pending");
      return;
    }
    if (!auth.currentOrg) {
      router.replace("/signin?step=org");
      return;
    }
    router.replace("/workspace/");
  }, [auth, router]);

  return (
    <div className="eo-shell" style={{ display: "grid", placeItems: "center" }}>
      <p className="eo-mute">Loading…</p>
    </div>
  );
}
