"use client";

import { useTheme } from "@/lib/useTheme";
import { MoonIcon, SunIcon } from "./icons";
import styles from "./ThemeToggle.module.css";

export function ThemeToggle() {
  const [theme, toggle] = useTheme();
  return (
    <button
      type="button"
      className={styles.toggle}
      onClick={toggle}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
    >
      {theme === "dark" ? <SunIcon size={15} /> : <MoonIcon size={15} />}
      <span>{theme === "dark" ? "Light" : "Dark"}</span>
    </button>
  );
}
