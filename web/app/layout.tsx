import type { Metadata } from "next";
import "./globals.css";
import Nav from "@/components/Nav";

export const metadata: Metadata = {
  title: "Project Radar",
  description:
    "Персональный технический радар: найти → проверить → сопоставить с проектом → оценить пользу",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>
        <div className="shell">
          <Nav />
          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
