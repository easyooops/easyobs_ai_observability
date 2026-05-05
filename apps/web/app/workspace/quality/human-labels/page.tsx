import { redirect } from "next/navigation";

/** Legacy URL; human labels live under Golden Sets → Human labels tab. */
export default function HumanLabelsRedirectPage() {
  redirect("/workspace/quality/golden/?tab=labels");
}
