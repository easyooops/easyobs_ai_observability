import { Suspense } from "react";
import { TraceDetailInner } from "./inner";

export default function Page() {
  return (
    <Suspense fallback={<div className="eo-empty">Loading trace…</div>}>
      <TraceDetailInner />
    </Suspense>
  );
}
