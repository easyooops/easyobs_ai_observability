import { redirect } from "next/navigation";

export default function LegacyCostRedirect() {
  redirect("/workspace/");
}
