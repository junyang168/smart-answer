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
import { Playlist } from "@/app/components/playlist";


async function HomeComp() {
  let org_id:string = process.env.ORG_ID || "";
  let env = process.env.NODE_ENV;
  
  const session = env === "production"? await getServerSession(authConfig) : '1234';

  const rid= nanoid()
  if(session) {
    return (
      <Playlist org_id={org_id} rid={rid}  ></Playlist>
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
