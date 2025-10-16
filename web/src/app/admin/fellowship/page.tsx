import { redirect } from "next/navigation";
import { getServerSession } from "next-auth";
import { authConfig } from "@/app/utils/auth";
import { FellowshipManager } from "@/app/components/admin/fellowship/FellowshipManager";

export const dynamic = "force-dynamic";

export default async function FellowshipAdminPage() {


  return (
    <div className="min-h-screen bg-gray-50 py-10">
      <div className="mx-auto max-w-5xl px-6">
        <FellowshipManager />
      </div>
    </div>
  );
}
