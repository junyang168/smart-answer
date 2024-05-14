"use client";
import { Result } from "@/app/components/result";
import { Search } from "@/app/components/search";
import { useSearchParams } from "next/navigation";
import { Suspense } from 'react'
import React, { useState } from "react";
import { nanoid } from "nanoid";
import { Header } from "@/app/components/header";

function SearchBar() {
  const searchParams = useSearchParams();
  const query = decodeURIComponent(searchParams.get("q") || "");
  const org_id = decodeURIComponent(searchParams.get("o") || "");
  const rid = decodeURIComponent(searchParams.get("rid") || "");
//  const [rid, setRid] = useState(nanoid());

  console.log('------------------------------------------------------------------10: ', rid, query, org_id);
  return (
    <div className="absolute inset-0 bg-[url('/bg.svg')]">
      <Header></Header>
      <div className="mx-auto max-w-3xl absolute inset-4 md:inset-8 bg-white">
        <div className="h-20 pointer-events-none rounded-t-2xl w-full backdrop-filter absolute top-0 bg-gradient-to-t from-transparent to-white [mask-image:linear-gradient(to_bottom,white,transparent)]"></div>
        <div className="px-4 md:px-8 pt-6 pb-24 rounded-2xl ring-8 ring-zinc-300/20 border border-zinc-200 h-full overflow-auto">
          <Result key={rid} org_id={org_id} query={query} rid={rid} ></Result>
        </div>
        <div className="h-80 pointer-events-none w-full rounded-b-2xl backdrop-filter absolute bottom-0 bg-gradient-to-b from-transparent to-white [mask-image:linear-gradient(to_top,white,transparent)]"></div>
        <div className="absolute z-10 flex items-center justify-center bottom-6 px-4 md:px-8 w-full">
          <div className="w-full">
              <Search org_id={org_id} rid={rid} followup="true" />
          </div>
        </div>
      </div>
    </div>

  );

}

export default function SearchPage() {
  return (
    // You could have a loading skeleton as the `fallback` too
    <Suspense fallback={<>Loading...</>}>
      <SearchBar />
    </Suspense>
  )  
}
