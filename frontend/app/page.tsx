import { Suspense } from "react";
import { Dashboard } from "@/components/Dashboard";

export default function Page() {
  return (
    <Suspense fallback={<div style={{ padding: 28 }}>Loading SightLine...</div>}>
      <Dashboard />
    </Suspense>
  );
}
