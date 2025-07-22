import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { ReactNode } from "react";
import Head from 'next/head';
import { Header } from "@/app/components/header";
import { Footer } from '@/app/components/footer';
import { Providers } from '@/app/components/providers/provider'; 

const inter = Inter({ subsets: ["latin"] });

import SessionProvider from "./SessionProvider"; //next SessionProvider imported
import { getServerSession } from "next-auth";

// components/Layout.tsx
import React from 'react';
import { Noto_Sans_TC, Merriweather } from 'next/font/google';

// Font configuration
const notoSansTC = Noto_Sans_TC({
  subsets: ['latin'],
  weight: ['400', '700'],
  variable: '--font-noto-sans-tc',
});

const merriweather = Merriweather({
  subsets: ['latin'],
  weight: ['400', '700'],
  variable: '--font-merriweather',
});

export const metadata: Metadata = {
  title: 'Dallas Holy Logos Church | 達拉斯聖道教會',
  description: 'A Chinese Christian church located in the Dallas area, committed to sound biblical teaching and discipleship.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={`${notoSansTC.variable} ${merriweather.variable} font-sans flex flex-col min-h-screen bg-gray-50`}>
        <Providers>
          <Header show_signin="True"/>
          <main className="flex-grow">
                {children}
          </main>
        <Footer />
        </Providers>
      </body>
    </html>
  );
}

