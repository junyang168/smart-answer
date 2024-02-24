"use client";
import { Footer } from "@/app/components/footer";
import { Logo } from "@/app/components/logo";
import { PresetQuery } from "@/app/components/preset-query";
import { Search } from "@/app/components/search";
import React from "react";
import { useSearchParams } from "next/navigation";

export default function Home() {
  const searchParams = useSearchParams();
  const org_id = decodeURIComponent(searchParams.get("o") || "");

  return (
    <div className="absolute inset-0 min-h-[500px] flex items-center justify-center">
      <div className="relative flex flex-col gap-8 px-4 -mt-24">
        <Logo></Logo>
          <Search org_id={org_id}></Search>
        <div className="flex gap-2 flex-wrap justify-center">
          <PresetQuery org_id={org_id} query="When will ESXi 7 go out of support?"></PresetQuery>
          <PresetQuery org_id={org_id} query="What are the steps to configure GPUs on esxi 8?"></PresetQuery>
        </div>
        <Footer></Footer>
      </div>
    </div>
  );
}
