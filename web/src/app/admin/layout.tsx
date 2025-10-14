import { ReactNode } from "react";
import { redirect } from "next/navigation";
import { getServerSession } from "next-auth";
import { AdminHeader } from "@/app/components/admin/AdminHeader";
import { authConfig } from "@/app/utils/auth";

export default async function AdminLayout({ children }: { children: ReactNode }) {
  
  const session = await getServerSession(authConfig);


  if (process.env.NODE_ENV === "production") {
    if (!session) {
      redirect(`/api/auth/signin?callbackUrl=${encodeURIComponent("/admin")}`);
    }
    const role = session.user?.role;
    if (role !== "editor" && role !== "admin") {
      redirect("/");
    }
  }

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
