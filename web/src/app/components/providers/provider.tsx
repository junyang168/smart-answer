"use client"; // ✅ 這是最關鍵的一步！

import { SessionProvider } from "next-auth/react";

type Props = {
  children?: React.ReactNode;
};

// 這個組件現在是一個客戶端組件，它的唯一職責就是渲染所有需要在客戶端運行的 Provider
export const Providers = ({ children }: Props) => {
  return <SessionProvider>{children}</SessionProvider>;
  // 如果未來您有其他全局 Provider (如 ThemeProvider)，也可以包裹在這裡
  // return (
  //   <SessionProvider>
  //     <ThemeProvider>
  //       {children}
  //     </ThemeProvider>
  //   </SessionProvider>
  // );
};