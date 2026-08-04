import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "王守仁教授釋經與專題講論文庫",
  description:
    "按聖經經卷與講論專題，整理王守仁教授在不同時期、不同場合的釋經內容。",
};

export default function WangRepositoryLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return children;
}
