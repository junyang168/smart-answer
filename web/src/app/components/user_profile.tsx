import { getServerSession } from "next-auth";
import React, { FC } from "react";
import Image from 'next/image'
import {
    GoogleSignInButton,
  } from "@/app/components/signinButtons";
import { authConfig} from "@/app/utils/auth";
import { useSession } from "next-auth/react";


export const UserProfile_with_signin: FC = async () => {
    const session = await getServerSession(authConfig);
    if (session && session.user) {
        const fullName = session.user.name;
        const image = String(session.user.image) ;
        return (
            <div className="flex items-center justify-right" id="user_info">
                <span className="text-sm font-semibold text-black mr-2">{fullName}</span>
                <Image className="h-8 w-8 rounded-full object-cover" src={image} alt="User Profile Picture" width='24' height='24' />
            </div>
        )
    }
    else {
        return (
            <GoogleSignInButton />
        )
    }
};


export const UserProfile: FC =  () => {
    const { data: session, status } = useSession();
    if (status == 'authenticated' && session.user) {
        const fullName = session.user.name;
        const image = String(session.user.image) ;

        return (
            <div className="flex items-center justify-right" id="user_info">
                <span className="text-sm font-semibold text-black mr-2">{fullName}</span>
                <Image className="h-8 w-8 rounded-full object-cover" src={image} alt="User Profile Picture" width='24' height='24' />
            </div>
        )
    }
};
