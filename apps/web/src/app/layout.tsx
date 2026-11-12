import type { Metadata } from "next";
import { Navbar } from "@/components/Navbar";
import "./globals.css";

export const metadata: Metadata = {
  title: "COUNTER",
  description: "Phone ordering for independent takeaways, paid through PayPal before the kitchen starts.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full bg-cream-50">
      <body className="min-h-full flex flex-col font-sans antialiased text-stone-900">
        <Navbar />
        <main id="main" className="flex-1">
          {children}
        </main>
      </body>
    </html>
  );
}
