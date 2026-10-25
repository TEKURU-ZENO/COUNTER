import type { Metadata } from "next";
import { Navbar } from "@/components/Navbar";
import "./globals.css";

export const metadata: Metadata = {
  title: "COUNTER — AI Phone Agent for Independent Takeaways",
  description: "An AI phone agent for independent takeaway restaurants that takes the order and moves money through PayPal before the caller hangs up. Only paid orders reach the kitchen.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full bg-cream-50">
      <body className="h-full flex flex-col font-sans antialiased text-stone-900">
        <Navbar />
        <main className="flex-1">{children}</main>
      </body>
    </html>
  );
}
