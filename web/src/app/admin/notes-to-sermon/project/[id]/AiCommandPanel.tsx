"use client";

import React, { useState } from 'react';

interface AiCommandPanelProps {
    selectedText: string;
    onRefine: (instruction: string) => Promise<void>;
    isRefining: boolean;
}

export default function AiCommandPanel({ selectedText, onRefine, isRefining }: AiCommandPanelProps) {
    const [instruction, setInstruction] = useState("");

    const handleRefineClick = () => {
        if (!instruction.trim()) return;
        onRefine(instruction);
    };

    if (!selectedText) {
        return (
            <div className="bg-gray-50 border-l h-full p-4 w-[300px] flex flex-col items-center justify-center text-gray-400 text-sm text-center">
                <p>Select text in the editor to refine it with AI.</p>
            </div>
        );
    }

    return (
        <div className="bg-white border-l h-full flex flex-col w-[350px] shadow-xl z-20">
            <div className="p-4 bg-indigo-50 border-b">
                <h3 className="font-bold text-indigo-900 flex items-center gap-2">
                    ✨ AI Refinement
                </h3>
            </div>

            <div className="flex-1 overflow-auto p-4 space-y-4">
                {/* Context Preview */}
                <div className="bg-white border rounded p-3 text-xs text-gray-500 max-h-[150px] overflow-y-auto italic">
                    <span className="font-bold text-gray-700 not-italic block mb-1">Selected Context:</span>
                    &quot;{selectedText}&quot;
                </div>

                {/* Instruction Input */}
                <div className="space-y-2">
                    <label className="text-sm font-semibold text-gray-700">Instruction:</label>
                    <textarea
                        className="w-full border rounded p-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none min-h-[120px]"
                        placeholder="e.g., Make this more concise, or Integrate these notes: [Paste Content]..."
                        value={instruction}
                        onChange={(e) => setInstruction(e.target.value)}
                        disabled={isRefining}
                    />
                </div>

                {/* Hints */}
                <div className="text-xs text-gray-400 space-y-1">
                    <p>Tip: You can paste new content into the instruction box to merge it.</p>
                </div>
            </div>

            <div className="p-4 border-t bg-gray-50">
                <button
                    onClick={handleRefineClick}
                    disabled={isRefining || !instruction.trim()}
                    className={`w-full py-2 px-4 rounded font-bold text-white transition-all
                        ${isRefining || !instruction.trim()
                            ? 'bg-gray-400 cursor-not-allowed'
                            : 'bg-indigo-600 hover:bg-indigo-700 shadow-lg'}`}
                >
                    {isRefining ? (
                        <span className="flex items-center justify-center gap-2">
                            <span className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></span>
                            Refining...
                        </span>
                    ) : (
                        "Refine Selection"
                    )}
                </button>
            </div>
        </div>
    );
}
