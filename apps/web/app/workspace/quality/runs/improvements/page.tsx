import { redirect } from "next/navigation";

/** Legacy URL — Improvement Pack lives under /workspace/quality/improvements/ */
export default function LegacyRunsImprovementsRedirect() {
  redirect("/workspace/quality/improvements/");
}
