import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "DocuLens AI | Evidence Workspace",
  description: "Search your PDFs and verify answers against the original source pages.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
