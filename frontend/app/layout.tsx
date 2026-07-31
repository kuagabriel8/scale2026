import type { Metadata } from "next";
import Script from "next/script";
import { TopNav } from "@/components/TopNav";
import "./globals.css";

export const metadata: Metadata = {
  title: "SightLine - AML/Fraud Triage",
  description:
    "Live severity-ranked transaction risk assessments for AML/fraud triage analysts.",
};

// Sets data-theme before hydration to avoid a light/dark flash. Reads a
// stored manual preference first, then falls back to prefers-color-scheme.
const themeInitScript = `
(function () {
  try {
    var stored = localStorage.getItem("sightline-theme");
    var theme = stored === "light" || stored === "dark"
      ? stored
      : (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    document.documentElement.setAttribute("data-theme", theme);
  } catch (e) {}
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body suppressHydrationWarning>
        <Script
          id="sightline-theme-init"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{ __html: themeInitScript }}
        />
        <TopNav />
        {children}
      </body>
    </html>
  );
}
