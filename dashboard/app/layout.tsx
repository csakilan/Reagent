import type { Metadata } from "next";
import "./globals.css";
import UserSwitcher from "./user-switcher";
import Link from "next/link";
import Image from "next/image";

export const metadata: Metadata = { title: "ReAgent · Policy resolution", description: "Resolved agent policy with layer provenance." };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><header className="topbar">
    <Link className="topbar-brand" href="/" aria-label="ReAgent home"><Image src="/reagent.svg" alt="ReAgent" width={34} height={34} priority /><span>ReAgent</span></Link>
    <div className="topbar-context"><span>Policy control plane</span></div>
    <div className="topbar-tools"><UserSwitcher /><Link href="/privacy">Privacy</Link></div>
  </header>{children}</body></html>;
}
