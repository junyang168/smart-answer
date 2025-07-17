// components/sermons/ScriptureHover.tsx
"use client";

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/app/components/hover-card";


interface ScriptureHoverProps {
  reference: string; // e.g., "羅馬書 1:16-17"
  text : string;
}

export const ScriptureHover = ({ reference, text }: ScriptureHoverProps) => {
  return (
    <HoverCard openDelay={200} closeDelay={100}>
      <HoverCardTrigger asChild>
        {/* 這就是用戶看到的經文引用，帶有下劃線提示可交互 */}
        <span className="cursor-pointer border-b border-dotted border-gray-500 hover:text-[#D4AF37]">
          {reference}
        </span>
      </HoverCardTrigger>
      <HoverCardContent className="w-80 bg-white shadow-lg z-50">
        <div className="space-y-2">
          <h4 className="font-bold text-gray-900">{reference}</h4>
          <p className="text-sm text-gray-700 leading-relaxed">
            {text}
          </p>
        </div>
      </HoverCardContent>
    </HoverCard>
  );
};