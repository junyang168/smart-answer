// components/admin/qa/QAListPanel.tsx
import { FaithQA } from '@/app/interfaces/article';
import { Plus } from 'lucide-react';

interface QAListPanelProps {
    qas: FaithQA[];
    selectedId: string | null;
    onSelect: (id: string) => void;
    onAddNew: () => void;
}

export const QAListPanel = ({ qas, selectedId, onSelect, onAddNew }: QAListPanelProps) => {
    // 這裡可以添加搜索過濾邏輯
    return (
        <div className="flex flex-col h-full">
            <div className="p-4 border-b">
                <h1 className="text-xl font-bold">問答編輯器</h1>
                <button onClick={onAddNew} className="w-full mt-4 flex items-center justify-center gap-2 bg-blue-600 text-white font-semibold py-2 rounded-md hover:bg-blue-700">
                    <Plus className="w-5 h-5"/>
                    新增問答
                </button>
            </div>
            <ul className="flex-1 overflow-y-auto">
                {qas.map(qa => (
                    <li key={qa.id}>
                        <button
                            onClick={() => onSelect(qa.id)}
                            className={`w-full text-left p-4 border-l-4 ${selectedId === qa.id ? 'border-blue-600 bg-blue-50' : 'border-transparent hover:bg-gray-50'}`}
                        >
                            <p className="font-semibold truncate">{qa.question}</p>
                            <p className="text-xs text-gray-500">{qa.category} • {new Date(qa.createdAt).toLocaleDateString()}</p>
                        </button>
                    </li>
                ))}
            </ul>
        </div>
    );
};