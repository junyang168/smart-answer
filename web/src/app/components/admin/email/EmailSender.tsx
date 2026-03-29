'use client';

import React, { useState, useMemo } from 'react';
import dynamic from 'next/dynamic';
import 'react-quill/dist/quill.snow.css';

const ReactQuill = dynamic(() => import('react-quill'), { ssr: false });

type ComposeMode = 'rich' | 'html';

function looksLikeHtmlDocument(value: string): boolean {
    return /<!doctype html|<html[\s>]|<head[\s>]|<body[\s>]/i.test(value);
}

function extractTitleFromHtml(value: string): string {
    const match = value.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
    if (!match?.[1]) {
        return '';
    }
    return match[1].replace(/\s+/g, ' ').trim();
}

export default function EmailSender() {
    const [subject, setSubject] = useState('');
    const [richBody, setRichBody] = useState('');
    const [htmlBody, setHtmlBody] = useState('');
    const [composeMode, setComposeMode] = useState<ComposeMode>('html');
    const [status, setStatus] = useState<'idle' | 'sending' | 'success' | 'error'>('idle');
    const [message, setMessage] = useState('');

    const modules = useMemo(() => ({
        toolbar: [
            [{ 'header': [1, 2, false] }],
            ['bold', 'italic', 'underline', 'strike', 'blockquote'],
            [{ 'list': 'ordered' }, { 'list': 'bullet' }, { 'indent': '-1' }, { 'indent': '+1' }],
            ['link', 'image'],
            ['clean']
        ],
    }), []);

    const formats = [
        'header',
        'bold', 'italic', 'underline', 'strike', 'blockquote',
        'list', 'bullet', 'indent',
        'link', 'image'
    ];

    const activeBody = composeMode === 'html' ? htmlBody : richBody;
    const previewInFrame = composeMode === 'html' || looksLikeHtmlDocument(activeBody);

    const handleSend = async () => {
        const trimmedBody = activeBody.trim();
        const resolvedSubject = subject.trim() || (composeMode === 'html' ? extractTitleFromHtml(activeBody) : '');

        if (!resolvedSubject || !trimmedBody) {
            setMessage('Please fill in both subject and body.');
            setStatus('error');
            return;
        }

        if (!confirm('Are you sure you want to send this email to the congregation?')) {
            return;
        }

        setStatus('sending');
        setMessage('');

        try {
            const response = await fetch('/api/admin/email/send', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    subject: resolvedSubject,
                    body: trimmedBody,
                    recipients_type: 'congregation',
                }),
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Failed to send email');
            }

            const data = await response.json();
            setStatus('success');
            setMessage(`Email sent successfully to ${data.recipient_count} recipients.`);
            setSubject('');
            setRichBody('');
            setHtmlBody('');
        } catch (error: any) {
            setStatus('error');
            setMessage(error.message);
        }
    };

    return (
        <div className="p-6 max-w-4xl mx-auto">
            <h1 className="text-2xl font-bold mb-6">Send Email to Congregation</h1>

            <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-1">Subject</label>
                <input
                    type="text"
                    className="w-full p-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                    value={subject}
                    onChange={(e) => setSubject(e.target.value)}
                    placeholder={composeMode === 'html' ? 'Enter email subject, or leave blank to use <title>' : 'Enter email subject'}
                />
            </div>

            <div className="mb-6">
                <div className="mb-3 flex items-center gap-2">
                    <button
                        type="button"
                        onClick={() => setComposeMode('html')}
                        className={`rounded-md px-3 py-1.5 text-sm font-medium ${composeMode === 'html'
                            ? 'bg-blue-600 text-white'
                            : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                            }`}
                    >
                        Raw HTML
                    </button>
                    <button
                        type="button"
                        onClick={() => setComposeMode('rich')}
                        className={`rounded-md px-3 py-1.5 text-sm font-medium ${composeMode === 'rich'
                            ? 'bg-blue-600 text-white'
                            : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                            }`}
                    >
                        Rich Editor
                    </button>
                </div>

                <label className="block text-sm font-medium text-gray-700 mb-1">Body</label>
                <p className="mb-3 text-sm text-gray-500">
                    {composeMode === 'html'
                        ? 'Paste the full email HTML here. Full documents with <!DOCTYPE>, <head>, and inline styles are sent as-is.'
                        : 'Compose the email visually. This editor outputs HTML that will be sent as the email body.'}
                </p>
                <div className="bg-white">
                    {composeMode === 'html' ? (
                        <textarea
                            className="min-h-[22rem] w-full rounded-md border border-gray-300 p-3 font-mono text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                            value={htmlBody}
                            onChange={(e) => setHtmlBody(e.target.value)}
                            placeholder="Paste your full HTML email here"
                            spellCheck={false}
                        />
                    ) : (
                        <ReactQuill
                            theme="snow"
                            value={richBody}
                            onChange={setRichBody}
                            modules={modules}
                            formats={formats}
                            className="h-64 mb-12"
                        />
                    )}
                </div>
            </div>

            <div className="flex items-center justify-between mt-12">
                <button
                    onClick={handleSend}
                    disabled={status === 'sending'}
                    className={`px-4 py-2 rounded-md text-white font-medium ${status === 'sending'
                        ? 'bg-gray-400 cursor-not-allowed'
                        : 'bg-blue-600 hover:bg-blue-700'
                        }`}
                >
                    {status === 'sending' ? 'Sending...' : 'Send Email'}
                </button>

                {message && (
                    <div
                        className={`px-4 py-2 rounded-md ${status === 'success' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                            }`}
                    >
                        {message}
                    </div>
                )}
            </div>

            {/* Preview Section */}
            <div className="mt-8 border-t pt-6">
                <h2 className="text-lg font-semibold mb-4">Preview</h2>
                {previewInFrame ? (
                    <iframe
                        title="HTML email preview"
                        srcDoc={activeBody}
                        className="min-h-[420px] w-full rounded-md border border-gray-200 bg-white"
                        sandbox=""
                    />
                ) : (
                    <div className="border border-gray-200 rounded-md p-4 min-h-[200px] bg-white prose max-w-none">
                        <div dangerouslySetInnerHTML={{ __html: activeBody }} />
                    </div>
                )}
            </div>
        </div>
    );
}
