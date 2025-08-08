"use client";
import { useState } from 'react';
import React, { FC } from "react";
import { UserProfile, UserProfile_with_signin } from "./user_profile";  
import Image from 'next/image';
import Link from 'next/link';
import { Search, Menu, X, ChevronDown } from 'lucide-react';
import { AuthButton } from '@/app/components/common/AuthButton';

const NavLink = ({ href, children }: { href: string; children: React.ReactNode }) => (
  <Link href={href} className="text-gray-700 hover:text-[#D4AF37] transition-colors duration-300">
    {children}
  </Link>
);



export const Header: FC<{ show_signin: string }> = ({ show_signin }) => {
  const [isMenuOpen, setIsMenuOpen] = useState(false);

  return (
    <header className="bg-white/95 backdrop-blur-sm sticky top-0 z-50 shadow-md">
      <div className="container mx-auto px-6 py-3 flex justify-between items-center">
        {/* Logo and Church Name */}
        <Link href="/" className="flex items-center gap-2">
          <Image src="/dhl_logo.jpg" alt="Dallas Holy Logos Church Logo" width={280} height={115} />
          <div>
          </div>
        </Link>

        {/* Desktop Navigation */}
        <nav className="hidden lg:flex items-center gap-6 font-medium">
          <NavLink href="/">首頁</NavLink>
          <NavLink href="/about">關於我們</NavLink>
          <NavLink href="/events">聚會與活動</NavLink>
          <NavLink href="/ministries">事工介紹</NavLink>
          <NavLink href="/resources">AI 輔助查經</NavLink>
          <NavLink href="/contact">聯絡我們</NavLink>
        </nav>

        {/* Desktop Action Buttons */}
        <div className="hidden lg:flex items-center gap-4">
          <button className="text-gray-600 hover:text-black">
            <Search size={20} />
          </button>

          <AuthButton />
        </div>

        {/* Mobile Menu Button */}
        <div className="lg:hidden">
          <button onClick={() => setIsMenuOpen(!isMenuOpen)} aria-label="Open Menu">
            {isMenuOpen ? <X size={28} /> : <Menu size={28} />}
          </button>
        </div>
      </div>

      {/* Mobile Menu */}
      {isMenuOpen && (
        <div className="lg:hidden bg-white w-full absolute shadow-xl">
          <nav className="flex flex-col items-center gap-4 py-8">
            <NavLink href="/">首頁</NavLink>
            <NavLink href="/about">關於我們</NavLink>
            <NavLink href="/events">聚會與活動</NavLink>
            <NavLink href="/ministries">事工介紹</NavLink>
            <NavLink href="/resources">AI 輔助查經</NavLink>
            <NavLink href="/contact">聯絡我們</NavLink>
            <div className="mt-4 border-t w-full flex justify-center pt-6 gap-6">
              <AuthButton />
            </div>
          </nav>
        </div>
      )}
    </header>
  );
};

