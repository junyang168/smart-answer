import { WangNav } from "./WangNav";

export default function WangAdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-[1440px] px-3 pb-12 sm:px-6">
      <div className="mb-6"><WangNav /></div>
      {children}
    </div>
  );
}
