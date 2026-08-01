import type { Metadata } from "next";
import { Mona_Sans } from "next/font/google";
import "./globals.css";

import { ThemeProvider } from "@/components/layout/theme-provider";
import { SidebarProvider } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { Header } from "@/components/layout/header";
import { HealthBanner } from "@/components/layout/health-banner";
import { Toaster } from "@/components/ui/sonner";
import { QueryClientProvider } from "@/lib/query-client";
import { RefreshProvider } from "@/lib/refresh-context";

// Display face — used for page titles. Body copy uses the system stack
// defined in globals.css.
const monaSans = Mona_Sans({
  variable: "--font-display",
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "Genblaze Media Studio",
  description:
    "One prompt → narrated, scored, captioned MP4. OpenAI + Decart + NVIDIA + GMICloud, orchestrated by Genblaze with Backblaze B2 as the sole asset store.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${monaSans.variable} antialiased`}>
        <ThemeProvider>
          <QueryClientProvider>
            <RefreshProvider>
              <SidebarProvider>
                <TooltipProvider>
                  <AppSidebar />
                  {/* `min-w-0` is load-bearing here. Without it, a flex
                      item defaults to `min-width: auto` (= min-content
                      width), so any wide descendant (the pipeline
                      canvas's intrinsic min-w-max row of tiles) pushes
                      THIS column wider than the viewport. The whole
                      page then horizontally scrolls — including the
                      Studio header — instead of only the canvas scrolling.
                      `overflow-x-hidden` on main is the belt-and-
                      suspenders: even if a descendant manages to claim
                      horizontal space, it stays clipped here so the
                      page header / sidebar stay anchored. */}
                  <div className="flex flex-1 flex-col min-w-0">
                    <Header />
                    <HealthBanner />
                    <main className="flex-1 overflow-y-auto overflow-x-hidden p-6 lg:p-8">
                      {children}
                    </main>
                  </div>
                  <Toaster />
                </TooltipProvider>
              </SidebarProvider>
            </RefreshProvider>
          </QueryClientProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
