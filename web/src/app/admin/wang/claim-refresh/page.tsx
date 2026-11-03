import type { Metadata } from "next";
import { ClaimRefreshProgress } from "./ClaimRefreshProgress";

export const metadata: Metadata = { title: "Claim 維護進度 | 王教授文庫", robots: { index: false, follow: false } };

export default function Page() { return <ClaimRefreshProgress />; }
