import React, { FC } from "react";
import Image from 'next/image'

export const Header: FC = () => {
  return (
    <header className="px-3 py-1 md:px-3 md:py-1 flex md:grid md:grid-cols-6 h-[48px] md:h-[48px]">
        <div className="flex-1 flex items-end justify-end md:col-start-6">
            <div className="flex items-center justify-right" id="user_info">
                <Image className="h-8 w-8 rounded-full object-cover" src="https://lh3.googleusercontent.com/ogw/AF2bZyiTK_-M2lzpxafPsQbqKSktTIEZ0VY_EfJJ4KyJhBC46r0=s32-c-mo" alt="User Profile Picture" width='24' height='24' />
            </div>
            <div id="g_id_onload" data-client_id="674603198442-bl7rkgeoiig3ei5uan4damdnliipt4sv.apps.googleusercontent.com"
                data-context="signin" data-ux_mode="popup" data-callback="onSignIn" data-auto_prompt="false">
            </div>
            <div className="g_id_signin" data-type="standard" data-shape="rectangular" data-theme="outline"
                data-text="signin_with" data-size="large" data-logo_alignment="left">
            </div>
        </div>
    </header>
  );
};
