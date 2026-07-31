// Formats model-predicted case-duration hours (which run from the
// thousands into the hundreds-of-thousands for this dataset) into a
// human-readable "N hrs (~M yrs)"-style string, so a viewer isn't left
// staring at a bare six-digit hour count.

const HOURS_PER_DAY = 24;
const HOURS_PER_YEAR = 24 * 365.25;

/** e.g. 12459.24 -> "12,459 hrs (~1.4 yrs)"; 18.5 -> "18.5 hrs"; 40 -> "40 hrs (~1.7 days)" */
export function formatPredictedHours(hours: number): string {
  const rounded = Math.round(hours * 10) / 10;
  const wholeHrs = Math.round(hours).toLocaleString();

  if (hours < HOURS_PER_DAY) {
    return `${rounded.toLocaleString()} hrs`;
  }
  if (hours < HOURS_PER_YEAR) {
    const days = Math.round((hours / HOURS_PER_DAY) * 10) / 10;
    return `${wholeHrs} hrs (~${days.toLocaleString()} days)`;
  }
  const years = Math.round((hours / HOURS_PER_YEAR) * 10) / 10;
  return `${wholeHrs} hrs (~${years.toLocaleString()} yrs)`;
}

/** Short year-forward summary for headline use, e.g. "~64.2 years". */
export function formatYearsHeadline(hours: number): string {
  const years = hours / HOURS_PER_YEAR;
  if (years < 1) {
    const days = hours / HOURS_PER_DAY;
    return days < 1 ? `~${(Math.round(hours * 10) / 10).toLocaleString()} hours` : `~${(Math.round(days * 10) / 10).toLocaleString()} days`;
  }
  return `~${(Math.round(years * 10) / 10).toLocaleString()} years`;
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
