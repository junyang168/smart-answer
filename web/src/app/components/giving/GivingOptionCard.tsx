// components/giving/GivingOptionCard.tsx
'use client';

import { LucideIcon, Copy, Check } from 'lucide-react';
import { useState } from 'react';

interface Detail {
    label: string;
    value: string;
    isCopiable: boolean;
}

interface GivingOptionCardProps {
    method: string;
    icon: LucideIcon;
    details: Detail[];
    instructions: string;
}

export const GivingOptionCard = ({ method, icon: Icon, details, instructions }: GivingOptionCardProps) => {
    const [copiedItem, setCopiedItem] = useState<string | null>(null);

    const handleCopy = (textToCopy: string, label: string) => {
        navigator.clipboard.writeText(textToCopy).then(() => {
            setCopiedItem(label);
            setTimeout(() => setCopiedItem(null), 2000); // 2秒後恢復圖標
        }).catch(err => {
            console.error('Failed to copy text: ', err);
        });
    };

    return (
        <div className="bg-white p-8 rounded-lg shadow-lg border">
            <div className="flex items-center gap-4 mb-6">
                <Icon className="w-8 h-8 text-blue-600 flex-shrink-0" />
                <h3 className="text-2xl font-bold text-gray-800">{method}</h3>
            </div>
            
            <div className="space-y-4 mb-6">
                {details.map(detail => (
                    <div key={detail.label} className="flex flex-col sm:flex-row sm:items-center sm:justify-between">
                        <span className="text-sm font-semibold text-gray-500">{detail.label}:</span>
                        <div className="flex items-center gap-2 mt-1 sm:mt-0">
                            <span className="font-mono bg-gray-100 p-2 rounded-md text-gray-800 text-sm">
                                {detail.value}
                            </span>
                            {detail.isCopiable && (
                                <button 
                                    onClick={() => handleCopy(detail.value, detail.label)} 
                                    className="p-2 text-gray-500 hover:bg-gray-200 rounded-md"
                                    title={`複製 ${detail.label}`}
                                >
                                        <Copy className="w-5 h-5" />
                                </button>
                            )}
                        </div>
                    </div>
                ))}
            </div>

            <p className="text-sm text-gray-600 bg-gray-50 p-4 rounded-md border">
                {instructions}
            </p>
        </div>
    );
};