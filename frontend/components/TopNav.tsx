"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import styles from "./TopNav.module.css";

const TABS = [
  { href: "/", label: "Risk assessment" },
  { href: "/time-prediction", label: "Case-time prediction" },
] as const;

/** Minimal top-level tab strip shared by every route - the two features
 * have no shared data/schema, this is navigation only. */
export function TopNav() {
  const pathname = usePathname();
  return (
    <nav className={styles.wrap} aria-label="Primary">
      {TABS.map((tab) => (
        <Link
          key={tab.href}
          href={tab.href}
          className={styles.tab}
          data-active={pathname === tab.href}
        >
          {tab.label}
        </Link>
      ))}
    </nav>
  );
}
