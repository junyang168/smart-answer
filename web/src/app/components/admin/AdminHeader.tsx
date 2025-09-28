import Image from "next/image";
import Link from "next/link";

export const AdminHeader = () => {
  return (
    <header className="sticky top-0 z-40 border-b border-gray-200 bg-white/90 backdrop-blur-sm">
      <div className="container mx-auto flex h-14 items-center justify-between px-6">
        <Link href="/" className="flex items-center gap-3">
          <Image
            src="/dhl_logo.jpg"
            alt="Dallas Holy Logos Church"
            width={140}
            height={60}
            className="h-9 w-auto"
          />
          <span className="text-sm font-semibold text-gray-700">Admin</span>
        </Link>
      </div>
    </header>
  );
};
