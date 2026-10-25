import type { Metadata } from "next";
import "./globals.css";
import Shell from "./shell";

export const metadata: Metadata = { title: "ReAgent · Policy control plane", description: "Self-hosted enterprise agents with layered, tighten-only policy." };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><Shell>{children}</Shell></body></html>;
}
