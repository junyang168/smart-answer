"use client";
import { getSearchUrl } from "@/app/utils/get-search-url";
import { ArrowRight } from "lucide-react";
import { useRouter } from "next/navigation";
import React, { FC, useState } from "react";

export const Search: FC<{org_id:string , rid:string , followup:string}> = ({org_id,rid, followup}) => {
  const [value, setValue] = useState("");
  const router = useRouter();
  const msg = followup == 'true' ? "Ask follow-up question..." : "Ask Smart Answer about ..."

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (value) {
          setValue("");
          
          router.push(getSearchUrl(org_id, encodeURIComponent(value), rid));
        }
      }}
    >
      <label
        className="relative bg-white flex items-center justify-center border ring-8 ring-zinc-300/20 py-2 px-2 rounded-lg gap-2 focus-within:border-zinc-300"
        htmlFor="search-bar"
      >
        <input
          id="search-bar"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoFocus
          placeholder={msg}
          className="px-2 pr-6 w-full rounded-md flex-1 outline-none bg-white"
          size={60}
        />
        <button
          type="submit"
          className="w-auto py-1 px-2 bg-black border-black text-white fill-white active:scale-95 border overflow-hidden relative rounded-xl"
        >
          <ArrowRight size={16} />
        </button>
      </label>
    </form>
  );
};
