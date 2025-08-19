// components/sermons/SermonSearchBar.tsx
"use client";

import { useState } from 'react'; // ✅ 引入 useState
import { usePathname, useSearchParams, useRouter } from 'next/navigation';
import { Search,Loader2 } from 'lucide-react';

// ✅ 1. 定義 props 類型
interface SermonSearchBarProps {
    isSearching: boolean;
}

export const SermonSearchBar = ({ isSearching }: SermonSearchBarProps) => {
    const searchParams = useSearchParams();
    const pathname = usePathname();
    const { replace } = useRouter();
    
    // ✅ 1. 创建一个本地 state 来管理输入框的实时值
    //    它的初始值来自 URL 参数，以支持页面刷新和分享链接
    const [inputValue, setInputValue] = useState(searchParams.get('q')?.toString() || '');

    // ✅ 2. 创建一个 form submit 处理器
    const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
        e.preventDefault(); // 阻止表单默认的整页刷新行为
        
        const params = new URLSearchParams(searchParams);
        params.set('page', '1');
        
        if (inputValue) {
            params.set('q', inputValue);
        } else {
            params.delete('q');
        }
        
        // 只有在提交时才更新 URL
        replace(`${pathname}?${params.toString()}`);
    };

    return (
        // ✅ 3. 将 input 包裹在一个 <form> 标签中
        <form onSubmit={handleSubmit} className="relative mb-8">
            <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <Search className="w-5 h-5 text-gray-400" />
            </div>
            <input
                type="text"
                placeholder="輸入查詢詞後按 Enter 鍵搜索..." // 更新 placeholder 提示用戶
                // ✅ 4. input 现在是一个受控组件，绑定到本地 state
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                // ✅ 3. 在搜索時禁用輸入框，防止用戶連續觸發
                disabled={isSearching}                
                className="w-full p-3 pl-10 border border-gray-300 rounded-lg shadow-sm focus:ring-2 focus:ring-[#D4AF37] focus:border-[#D4AF37]"
            />
            {/* 
              ✅ 4. 條件渲染 Spinner
              - 只有在 isSearching 為 true 時才顯示
              - `pr-12` (padding-right) 是為了給 spinner 預留空間
            */}
            {isSearching && (
                <div className="absolute inset-y-0 right-0 pr-4 flex items-center">
                    <Loader2 className="w-5 h-5 text-gray-500 animate-spin" />
                </div>
            )}            
            {/* 我们不再需要提交按钮，因为 Enter 键就可以触发表单提交 */}
        </form>
    );
};