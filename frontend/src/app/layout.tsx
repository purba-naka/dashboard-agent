import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Dashboard Studio",
  description: "Studio dashboard lokal berbasis agent",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="id">
      <body>{children}</body>
    </html>
  );
}
