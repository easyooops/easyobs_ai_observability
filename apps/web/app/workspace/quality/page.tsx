import { redirect } from "next/navigation";

/** Quality overview content lives on the unified workspace Overview. */
export default function QualityOverviewRedirect() {
  redirect("/workspace/");
}
