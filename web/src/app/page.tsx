import { Footer } from "@/app/components/footer";
import { Header } from "@/app/components/header";
import { Logo } from "@/app/components/logo";
import { PresetQuery } from "@/app/components/preset-query";
import { Search } from "@/app/components/search";
import React from "react";
import { useSearchParams } from "next/navigation";
import { Suspense } from 'react'
import { nanoid } from "nanoid";
import { getServerSession } from "next-auth";
import { authConfig} from "@/app/utils/auth";


async function HomeComp() {
  const org_id = process.env.ORG_ID;
  const session = await getServerSession(authConfig);
  const rid= nanoid()
  if(session) {
    return (
        <div className="absolute inset-0 min-h-[500px] flex items-center justify-center">
          <div className="relative flex flex-col gap-8 px-4 -mt-24">
              <Search org_id={org_id} rid={rid} followup="false" ></Search>
          </div>
        </div>    
    )
  }
  else {
    return (
      <h2>Please Log in </h2>
    )
  }
}


export default function Home() {
  return (
    <div>
      <Header show_signin="true"></Header>
      <HomeComp />
    </div>
  )  
}
