"use client";

import { useEffect, useState } from "react";

/** Forces a periodic re-render so relative timestamps ("3s ago") and
 * "recently updated" row highlighting stay live without a full data refetch. */
export function useNowTick(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
