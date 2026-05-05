import { redirect } from "next/navigation";

/** Metrics views are folded into Observe ▸ Overview. */
export default function MetricsRedirect() {
  redirect("/workspace/");
}
