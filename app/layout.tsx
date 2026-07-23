import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") || requestHeaders.get("host") || "localhost:3000";
  const protocol = requestHeaders.get("x-forwarded-proto") || (host.startsWith("localhost") ? "http" : "https");
  const origin = `${protocol}://${host}`;
  return {
    metadataBase: new URL(origin),
    title: "M2 Wallet — Stablecoin Operations Demo",
    description: "Interactive M2 Wallet demo for stablecoin payments, payouts, sweeping, finance approval, WaaS, and risk control.",
    manifest: "/demo/manifest.webmanifest",
    icons: { icon: "/demo/icon-192.png", apple: "/demo/icon-192.png" },
    openGraph: {
      title: "M2 Wallet",
      description: "Stablecoin Operations Demo · Payments · Payouts · Sweeping · Risk Control",
      type: "website",
      images: [{ url: `${origin}/og.png`, width: 1728, height: 907, alt: "M2 Wallet Stablecoin Operations Demo" }],
    },
    twitter: { card: "summary_large_image", title: "M2 Wallet", description: "Stablecoin Operations Demo", images: [`${origin}/og.png`] },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
