import type { Metadata } from "next";
import "./globals.css";
import UserSwitcher from "./user-switcher";
import Link from "next/link";

export const metadata: Metadata = { title: "ReAgent · Policy resolution", description: "Resolved agent policy with layer provenance." };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}<div className="global-user"><UserSwitcher /><Link href="/privacy">Privacy</Link></div></body></html>;
}
