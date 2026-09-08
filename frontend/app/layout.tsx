import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Test Case Generator",
  description: "Requirement-bound AI test case generation",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
