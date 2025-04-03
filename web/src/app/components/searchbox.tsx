"use client";
import { getSearchUrl } from "@/app/utils/get-search-url";
import { useRouter } from "next/navigation";
import React, { FC, useEffect, useState } from "react";
import { Search} from 'lucide-react';

export const SearchBox: FC<{org_id:string , rid:string }> = ({org_id,rid}) => {
  const [value, setValue] = useState("");
  const router = useRouter();

  return (
    <div className="flex-1 max-w-3xl mx-auto relative">
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (value) {
          setValue("");
          const url = getSearchUrl(org_id, encodeURIComponent(value), rid)                    
          router.push(url);
        }
      }}
    >
      <input
        type="text"
        placeholder="Search or Ask a question..."
        value={value}
        onChange={(e) => setValue(e.target.value)}
        autoFocus        
        className="w-full py-2 px-4 pr-12 bg-gray-100 rounded-full outline-none border border-gray-300"
      />
      <div className="absolute right-0 top-0 h-full flex items-center pr-3">
        <Search className="w-5 h-5 text-gray-500" />
      </div>
      </form>
      </div>


  );
};
