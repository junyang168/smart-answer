"use client";

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { ArrowLeft, User, Shield, Edit2, Trash2, Plus, X, ServerCrash } from 'lucide-react';

interface UserData {
    id: string; // Email
    name: string;
}

interface UserRole {
    user: string;
    role: string;
}

interface CombinedUser {
    email: string;
    name: string;
    role: string;
}

export default function UsersManagementPage() {
    const [users, setUsers] = useState<CombinedUser[]>([]);
    const [availableRoles, setAvailableRoles] = useState<string[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState('');
    
    // Modal State
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [modalMode, setModalMode] = useState<'add' | 'edit'>('add');
    const [formData, setFormData] = useState({ email: '', name: '', role: '' });
    const [isSaving, setIsSaving] = useState(false);

    const fetchData = async () => {
        setIsLoading(true);
        setError('');
        try {
            const res = await fetch('/api/admin/users');
            if (!res.ok) throw new Error('Failed to fetch data');
            const data = await res.json();
            
            // Combine users and roles
            const rulesMap = new Map<string, string>();
            (data.user_roles || []).forEach((ur: UserRole) => {
                rulesMap.set(ur.user.toLowerCase(), ur.role);
            });

            const combined: CombinedUser[] = (data.users || []).map((u: UserData) => ({
                email: u.id,
                name: u.name,
                role: rulesMap.get(u.id.toLowerCase()) || 'reader'
            }));

            setUsers(combined);
            setAvailableRoles(data.roles || ['admin', 'editor', 'reader', 'reviewer']);
        } catch (err: any) {
            setError(err.message || 'Something went wrong');
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        fetchData();
    }, []);

    const handleOpenModal = (mode: 'add' | 'edit', user?: CombinedUser) => {
        setModalMode(mode);
        if (mode === 'edit' && user) {
            setFormData({ email: user.email, name: user.name, role: user.role });
        } else {
            setFormData({ email: '', name: '', role: availableRoles[0] || 'reader' });
        }
        setIsModalOpen(true);
    };

    const handleCloseModal = () => {
        setIsModalOpen(false);
        setFormData({ email: '', name: '', role: '' });
    };

    const handleSave = async (e: React.FormEvent) => {
        e.preventDefault();
        setIsSaving(true);
        try {
            const res = await fetch('/api/admin/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    action: modalMode,
                    user: { id: formData.email.trim(), name: formData.name },
                    role: formData.role
                })
            });

            if (!res.ok) {
                const errorData = await res.json();
                throw new Error(errorData.error || 'Failed to save');
            }

            await fetchData();
            handleCloseModal();
        } catch (err: any) {
            alert(err.message);
        } finally {
            setIsSaving(false);
        }
    };

    const handleDelete = async (email: string) => {
        if (!confirm(`確定要刪除使用者 ${email} 嗎？`)) return;
        
        try {
            setIsLoading(true);
            const res = await fetch('/api/admin/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    action: 'delete',
                    user: { id: email }
                })
            });

            if (!res.ok) throw new Error('Failed to delete');
            await fetchData();
        } catch (err: any) {
            alert(err.message);
            setIsLoading(false); // only toggle if error since fetchData handles it otherwise
        }
    };

    return (
        <div className="min-h-screen bg-gray-50 py-12">
            <div className="mx-auto max-w-6xl px-6">
                <header className="mb-8 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
                    <div>
                        <h1 className="text-3xl font-bold text-gray-900">使用者管理</h1>
                        <p className="mt-2 text-gray-600">管理可以登入並使用本網站管理後台的人員名單與權限</p>
                    </div>
                    <div className="flex items-center gap-3">
                        <Link
                            href="/admin"
                            className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 transition"
                        >
                            <ArrowLeft className="h-4 w-4" />
                            返回
                        </Link>
                        <button
                            onClick={() => handleOpenModal('add')}
                            className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 transition"
                        >
                            <Plus className="h-4 w-4" />
                            新增使用者
                        </button>
                    </div>
                </header>

                {error ? (
                    <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-center shadow-sm">
                        <ServerCrash className="mx-auto h-12 w-12 text-red-400" />
                        <h3 className="mt-4 text-lg font-medium text-red-900">載入失敗</h3>
                        <p className="mt-2 text-sm text-red-600">{error}</p>
                        <button onClick={fetchData} className="mt-4 text-sm font-medium text-red-700 hover:underline">
                            重試
                        </button>
                    </div>
                ) : isLoading ? (
                    <div className="flex items-center justify-center p-12">
                        <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-600 border-t-transparent"></div>
                    </div>
                ) : (
                    <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
                        <div className="overflow-x-auto">
                            <table className="min-w-full divide-y divide-gray-200 text-left text-sm">
                                <thead className="bg-gray-50">
                                    <tr>
                                        <th className="px-6 py-4 font-medium text-gray-900">姓名</th>
                                        <th className="px-6 py-4 font-medium text-gray-900">電子信箱</th>
                                        <th className="px-6 py-4 font-medium text-gray-900">角色權限</th>
                                        <th className="px-6 py-4 text-right font-medium text-gray-900">操作</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-200 bg-white">
                                    {users.map((user) => (
                                        <tr key={user.email} className="hover:bg-gray-50 transition">
                                            <td className="px-6 py-4 font-medium text-gray-900">
                                                <div className="flex items-center gap-3">
                                                    <div className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-100 text-blue-600 font-bold">
                                                        {user.name.charAt(0).toUpperCase()}
                                                    </div>
                                                    {user.name}
                                                </div>
                                            </td>
                                            <td className="px-6 py-4 text-gray-600">{user.email}</td>
                                            <td className="px-6 py-4">
                                                <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium 
                                                    ${user.role === 'admin' ? 'bg-purple-100 text-purple-700' :
                                                      user.role === 'editor' ? 'bg-green-100 text-green-700' :
                                                      user.role === 'reviewer' ? 'bg-orange-100 text-orange-700' :
                                                      'bg-gray-100 text-gray-700'}`}
                                                >
                                                    <Shield className="h-3 w-3" />
                                                    {user.role}
                                                </span>
                                            </td>
                                            <td className="px-6 py-4 text-right">
                                                <div className="flex items-center justify-end gap-2">
                                                    <button
                                                        onClick={() => handleOpenModal('edit', user)}
                                                        className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-blue-600 transition"
                                                        title="編輯"
                                                    >
                                                        <Edit2 className="h-4 w-4" />
                                                    </button>
                                                    <button
                                                        onClick={() => handleDelete(user.email)}
                                                        className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-red-600 transition"
                                                        title="刪除"
                                                    >
                                                        <Trash2 className="h-4 w-4" />
                                                    </button>
                                                </div>
                                            </td>
                                        </tr>
                                    ))}
                                    {users.length === 0 && (
                                        <tr>
                                            <td colSpan={4} className="px-6 py-12 text-center text-gray-500">
                                                目前沒有建立任何使用者
                                            </td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}
            </div>

            {/* Modal */}
            {isModalOpen && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
                    <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
                        <div className="mb-4 flex items-center justify-between">
                            <h2 className="text-xl font-bold text-gray-900">
                                {modalMode === 'add' ? '新增使用者' : '編輯使用者'}
                            </h2>
                            <button onClick={handleCloseModal} className="rounded p-1 text-gray-400 hover:bg-gray-100">
                                <X className="h-5 w-5" />
                            </button>
                        </div>
                        
                        <form onSubmit={handleSave} className="space-y-4">
                            <div>
                                <label className="mb-1.5 block text-sm font-medium text-gray-700">電子信箱 (Email)</label>
                                <input
                                    type="email"
                                    required
                                    disabled={modalMode === 'edit'} // Don't allow email editing once created
                                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100 disabled:text-gray-500"
                                    value={formData.email}
                                    onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                                />
                                {modalMode === 'edit' && (
                                    <p className="mt-1 text-xs text-gray-500">電子信箱不可修改，若需修改請刪除後重新新增。</p>
                                )}
                            </div>
                            
                            <div>
                                <label className="mb-1.5 block text-sm font-medium text-gray-700">姓名 (Name)</label>
                                <input
                                    type="text"
                                    required
                                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                                    value={formData.name}
                                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                                />
                            </div>

                            <div>
                                <label className="mb-1.5 block text-sm font-medium text-gray-700">角色權限 (Role)</label>
                                <select
                                    required
                                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                                    value={formData.role}
                                    onChange={(e) => setFormData({ ...formData, role: e.target.value })}
                                >
                                    {availableRoles.map(r => (
                                        <option key={r} value={r}>{r}</option>
                                    ))}
                                </select>
                            </div>

                            <div className="mt-6 flex gap-3 pt-4">
                                <button
                                    type="button"
                                    onClick={handleCloseModal}
                                    className="flex-1 rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 transition"
                                >
                                    取消
                                </button>
                                <button
                                    type="submit"
                                    disabled={isSaving}
                                    className="flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition"
                                >
                                    {isSaving ? '儲存中...' : '儲存'}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    );
}
