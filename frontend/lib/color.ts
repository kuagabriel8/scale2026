function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.replace("#", "");
  const n = parseInt(clean, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rgbToHex(r: number, g: number, b: number): string {
  const clamp = (v: number) => Math.max(0, Math.min(255, Math.round(v)));
  return (
    "#" +
    [clamp(r), clamp(g), clamp(b)].map((v) => v.toString(16).padStart(2, "0")).join("")
  );
}

/** Interpolates between two hex colors. t in [0,1]. Used for the single
 * sequential-blue magnitude ramp (component_scores chart), never for
 * categorical/tier encodings which use fixed lookups instead. */
export function mixHex(hexA: string, hexB: string, t: number): string {
  const clampT = Math.max(0, Math.min(1, t));
  const [r1, g1, b1] = hexToRgb(hexA);
  const [r2, g2, b2] = hexToRgb(hexB);
  return rgbToHex(
    r1 + (r2 - r1) * clampT,
    g1 + (g2 - g1) * clampT,
    b1 + (b2 - b1) * clampT
  );
}
