// components/common/AuthButton.tsx
"use client";

import { signIn, signOut, useSession } from "next-auth/react";
import Image from "next/image";

export const AuthButton = () => {
  const { data: session, status } = useSession();

  if (status === "loading") {
    return <div className="w-24 h-9 bg-gray-200 rounded-full animate-pulse"></div>;
  }

  if (session) {
    return (
      <div className="flex items-center gap-3">
        <Image
          src={session.user?.image || ''}
          alt={session.user?.name || 'User Avatar'}
          width={32}
          height={32}
          className="rounded-full"
        />
        {/* ✅ 新增：顯示用戶姓名 */}
        <span className="text-sm font-semibold text-gray-700 hidden sm:block">
          {/* 
            - text-sm font-semibold: 清晰的字體樣式
            - hidden sm:block: 在超小屏幕 (sm 以下) 上隱藏姓名，只顯示頭像和按鈕，以節省空間
          */}
          {session.user?.name}
        </span>   
             
        <button
          onClick={() => signOut()}
          className="bg-gray-200 text-gray-800 font-semibold py-2 px-4 rounded-full hover:bg-gray-300 text-sm"
        >
          登出
        </button>
      </div>
    );
  }

  return (
    <button
      onClick={() => signIn("google")}
      className="bg-blue-500 text-white font-semibold py-2 px-4 rounded-full hover:bg-blue-600 text-sm"
    >
      使用 Google 登錄
    </button>
  );
};