import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "王守仁教授聖經講論文庫",
  description:
    "整理王守仁教授在不同時期、不同場合的聖經講論，按經卷與專題編排。",
};

export default function WangRepositoryLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return children;
}
