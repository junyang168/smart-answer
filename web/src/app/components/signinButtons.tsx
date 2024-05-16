"use client";
import Image from "next/image";
import googleLogo from "@/../public/google.png";
import { signIn } from "next-auth/react";

export function GoogleSignInButton() {

  return (
    <button
        type="button"
      onClick={() => {
        signIn("google");
      }}
      className="w-full flex items-center font-semibold justify-center h-8 px-6 mt-1 text-sm  transition-colors duration-300 bg-white  text-black focus:shadow-outline hover:bg-slate-200"
    >
      <Image src={googleLogo} alt="Google Logo" width={20} height={20} />
      <span className="ml-4">Sign in with Google</span>
    </button>
  );
}

