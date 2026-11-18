"use client";
import { hasEditorAccess } from "@/app/utils/roles";
import { useState } from 'react';
import React, { FC } from "react";
import Image from 'next/image';
import Link from 'next/link';
import { Menu, X } from 'lucide-react';
import { AuthButton } from '@/app/components/common/AuthButton';
import { useSession } from "next-auth/react";

const primaryLinks = [
  { href: "/", label: "首頁" },
  { href: "/about", label: "關於我們" },
  { href: "/resources", label: "聖經學習資源" },
  { href: "/ministries", label: "事工介紹" },
  { href: "/contact", label: "聯絡我們" },
];

const NavLink = ({ href, children, onNavigate }: {
  href: string;
  children: React.ReactNode;
  onNavigate?: () => void;
}) => (
  <Link href={href} onNavigate={onNavigate} className="text-gray-700 hover:text-[#D4AF37] transition-colors duration-300">
    {children}
  </Link>
);



export const Header: FC<{ show_signin: string }> = ({ show_signin }) => {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const { data: session } = useSession();
  const userRole = session?.user?.role;
  const canAccessAdmin = hasEditorAccess(userRole);
  const links = canAccessAdmin ? [...primaryLinks, { href: "/admin", label: "後臺管理" }] : primaryLinks;

  return (
    <header id="global-site-header" className="bg-white/95 backdrop-blur-sm sticky top-0 z-50 shadow-md">
      <div className="container mx-auto px-6 py-3 flex justify-between items-center">
        {/* Logo and Church Name */}
        <Link href="/" className="flex items-center gap-2">
          <Image src="/dhl_logo.jpg" alt="Dallas Holy Logos Church Logo" width={280} height={115} />
          <div>
          </div>
        </Link>

        {/* Desktop Navigation */}
        <nav className="hidden lg:flex items-center gap-6 font-medium">
          {links.map(({ href, label }) => <NavLink key={href} href={href}>{label}</NavLink>)}
        </nav>

        {/* Desktop Action Buttons */}
        <div className="hidden lg:flex items-center gap-4">


          <AuthButton />
        </div>

        {/* Mobile Menu Button */}
        <div className="lg:hidden">
          <button onClick={() => setIsMenuOpen(!isMenuOpen)} aria-label="Open Menu" aria-expanded={isMenuOpen}>
            {isMenuOpen ? <X size={28} /> : <Menu size={28} />}
          </button>
        </div>
      </div>

      {/* Mobile Menu */}
      {isMenuOpen && (
        <div className="lg:hidden bg-white w-full absolute shadow-xl">
          <nav className="flex flex-col items-center gap-4 py-8">
            {links.map(({ href, label }) => (
              <NavLink key={href} href={href} onNavigate={() => setIsMenuOpen(false)}>{label}</NavLink>
            ))}
            <div className="mt-4 border-t w-full flex justify-center pt-6 gap-6">
              <AuthButton />
            </div>
          </nav>
        </div>
      )}
    </header>
  );
};
