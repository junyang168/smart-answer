import React, { FC } from "react";
import { UserProfile, UserProfile_with_signin } from "./user_profile";  

export const Header: FC<{ show_signin: string }> = ({ show_signin }) => {
  return (
    <header className="px-3 py-1 md:px-3 md:py-1 flex md:grid md:grid-cols-6 h-[48px] md:h-[48px]">
        <div className="flex-1 flex items-end justify-end md:col-start-6">
          {
            show_signin == "true" ? <UserProfile_with_signin /> : <UserProfile />
          }
        </div>
    </header>
  );
};
