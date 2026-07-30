import type { ConnectionStatus } from "@/lib/types";
import { PulseIcon } from "./icons";
import styles from "./DataSourceStatus.module.css";

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  mock: "Mock data generator running",
  connecting: "Connecting to live stream...",
  connected: "Live - connected",
  reconnecting: "Live - reconnecting...",
  disconnected: "Live - disconnected",
  error: "Live - connection error",
};

export function DataSourceStatus({
  dataSource,
  status,
  eventCount,
}: {
  dataSource: "mock" | "live";
  status: ConnectionStatus;
  eventCount: number;
}) {
  const ok = status === "mock" || status === "connected";
  return (
    <div className={styles.wrap} data-ok={ok}>
      <PulseIcon size={13} className={ok ? styles.pulseOk : styles.pulseBad} />
      <span className={styles.label}>
        {dataSource === "mock" ? "MOCK MODE" : "LIVE MODE"}
      </span>
      <span className={styles.detail}>{STATUS_LABEL[status]}</span>
      <span className={styles.detail}>&middot; {eventCount} events received</span>
    </div>
  );
}
