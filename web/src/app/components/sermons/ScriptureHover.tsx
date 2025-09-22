// components/sermons/ScriptureHover.tsx
"use client";

import { useState } from 'react';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/app/components/popover";


interface ScriptureHoverProps {
  reference: string; // e.g., "羅馬書 1:16-17"
  text : string;
}

export const ScriptureHover = ({ reference, text }: ScriptureHoverProps) => {
  const [isOpen, setIsOpen] = useState(false);  
  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <PopoverTrigger asChild>
        {/* 
          ✅ 2. 在 Trigger 上同时绑定点击和鼠标事件
          - onClick: 在移动端，用户点击会切换 isOpen 状态
          - onMouseEnter: 在桌面端，鼠标进入时打开
          - onMouseLeave: 在桌面端，鼠标离开时关闭
        */}
        <span
          // 对于触摸设备 (移动端)，我们希望只响应点击
          // 对于非触摸设备 (桌面端)，我们希望响应悬停
          onClick={() => setIsOpen((v) => !v)}
          onMouseEnter={() => setIsOpen(true)}
          onMouseLeave={() => setIsOpen(false)}
          className="cursor-pointer border-b border-dotted border-gray-500 hover:text-[#D4AF37]"
          // aria-haspopup="true" // 增加可访问性
        >
          {reference}
        </span>
      </PopoverTrigger>
      <PopoverContent 
        className="w-80 bg-white shadow-lg z-50"
        // ✅ 3. 在内容区域也绑定鼠标事件
        // 这可以防止鼠标从 Trigger 移动到 Content 上时，弹窗意外关闭
        onMouseEnter={() => setIsOpen(true)}
        onMouseLeave={() => setIsOpen(false)}
      >
        <div className="space-y-2">
          <h4 className="font-bold text-gray-900">{reference}</h4>
          <p className="text-sm text-gray-700 leading-relaxed">
            {text}
          </p>
        </div>
      </PopoverContent>
    </Popover>
  );  
};