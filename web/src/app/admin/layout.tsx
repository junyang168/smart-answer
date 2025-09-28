import { ReactNode } from "react";
import { AdminHeader } from "@/app/components/admin/AdminHeader";

export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <style>{`#global-site-header { display: none !important; }`}</style>
      <div className="min-h-screen bg-gray-50">
        <AdminHeader />
        <main className="container mx-auto px-6 py-6">{children}</main>
      </div>
    </>
  );
}
