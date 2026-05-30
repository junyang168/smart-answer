"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { Plus, RefreshCw, Save, Search, Trash2 } from "lucide-react";
import {
  EmailRecipientsResponse,
  fetchEmailRecipients,
  updateEmailRecipients,
} from "@/app/admin/email/api";

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function normalizeLines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function dedupe(values: string[]): string[] {
  const seen = new Set<string>();
  const recipients: string[] = [];
  values.forEach((value) => {
    const key = value.toLowerCase();
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    recipients.push(value);
  });
  return recipients;
}

export function EmailRecipientsManager() {
  const [data, setData] = useState<EmailRecipientsResponse | null>(null);
  const [recipients, setRecipients] = useState<string[]>([]);
  const [newRecipient, setNewRecipient] = useState("");
  const [bulkText, setBulkText] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");

  const loadRecipients = async () => {
    setLoading(true);
    setError("");
    setFeedback("");
    try {
      const response = await fetchEmailRecipients();
      setData(response);
      setRecipients(response.recipients);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load recipients");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadRecipients();
  }, []);

  const invalidRecipients = useMemo(
    () => recipients.filter((recipient) => !EMAIL_PATTERN.test(recipient)),
    [recipients],
  );

  const filteredRecipients = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) {
      return recipients.map((recipient, index) => ({ recipient, index }));
    }
    return recipients
      .map((recipient, index) => ({ recipient, index }))
      .filter(({ recipient }) => recipient.toLowerCase().includes(normalized));
  }, [query, recipients]);

  const hasChanges = data !== null && recipients.join("\n") !== data.recipients.join("\n");

  const updateRecipientAt = (index: number, value: string) => {
    setFeedback("");
    setRecipients((current) => current.map((recipient, i) => (i === index ? value.trim() : recipient)));
  };

  const removeRecipientAt = (index: number) => {
    setFeedback("");
    setRecipients((current) => current.filter((_, i) => i !== index));
  };

  const addRecipient = (event: FormEvent) => {
    event.preventDefault();
    const value = newRecipient.trim();
    if (!value) {
      return;
    }
    setFeedback("");
    setRecipients((current) => dedupe([...current, value]));
    setNewRecipient("");
  };

  const importBulkRecipients = () => {
    const values = normalizeLines(bulkText);
    if (!values.length) {
      return;
    }
    setFeedback("");
    setRecipients((current) => dedupe([...current, ...values]));
    setBulkText("");
  };

  const saveRecipients = async () => {
    setError("");
    setFeedback("");
    if (!recipients.length) {
      setError("At least one recipient is required.");
      return;
    }
    if (invalidRecipients.length) {
      setError(`Fix invalid email addresses before saving: ${invalidRecipients.join(", ")}`);
      return;
    }

    setSaving(true);
    try {
      const response = await updateEmailRecipients(dedupe(recipients));
      setData(response);
      setRecipients(response.recipients);
      setFeedback(`Saved ${response.count} recipients.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save recipients");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <header className="mb-6 flex flex-col gap-4 border-b border-gray-200 pb-5 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Email Recipients</h1>
          <p className="mt-2 text-sm text-gray-600">
            Manage the congregation list used by fellowship reminders, Sunday Worship reminders, and admin email sends.
          </p>
          {data && (
            <p className="mt-1 text-xs text-gray-500">
              {data.production ? "Production" : "Test"} list: {data.file_path}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={loadRecipients}
            disabled={loading || saving}
            className="inline-flex items-center gap-2 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw className="h-4 w-4" />
            Refresh
          </button>
          <button
            type="button"
            onClick={saveRecipients}
            disabled={!hasChanges || loading || saving}
            className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-400"
          >
            <Save className="h-4 w-4" />
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </header>

      {(error || feedback) && (
        <div
          className={`mb-5 rounded-md px-4 py-3 text-sm ${
            error ? "bg-red-50 text-red-700" : "bg-green-50 text-green-700"
          }`}
        >
          {error || feedback}
        </div>
      )}

      <section className="mb-6 grid gap-4 lg:grid-cols-[1fr_1fr]">
        <form onSubmit={addRecipient} className="rounded-md border border-gray-200 bg-white p-4">
          <label className="block text-sm font-medium text-gray-700" htmlFor="new-recipient">
            Add recipient
          </label>
          <div className="mt-2 flex gap-2">
            <input
              id="new-recipient"
              type="email"
              value={newRecipient}
              onChange={(event) => setNewRecipient(event.target.value)}
              placeholder="name@example.com"
              className="min-w-0 flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <button
              type="submit"
              className="inline-flex items-center gap-2 rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800"
            >
              <Plus className="h-4 w-4" />
              Add
            </button>
          </div>
        </form>

        <div className="rounded-md border border-gray-200 bg-white p-4">
          <label className="block text-sm font-medium text-gray-700" htmlFor="bulk-recipients">
            Bulk import
          </label>
          <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_auto]">
            <textarea
              id="bulk-recipients"
              value={bulkText}
              onChange={(event) => setBulkText(event.target.value)}
              placeholder="Paste one email per line"
              rows={3}
              className="min-w-0 rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <button
              type="button"
              onClick={importBulkRecipients}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              <Plus className="h-4 w-4" />
              Import
            </button>
          </div>
        </div>
      </section>

      <section className="rounded-md border border-gray-200 bg-white">
        <div className="flex flex-col gap-3 border-b border-gray-200 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Recipients</h2>
            <p className="text-sm text-gray-500">
              {loading ? "Loading..." : `${recipients.length} total, ${filteredRecipients.length} shown`}
            </p>
          </div>
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search recipients"
              className="w-full rounded-md border border-gray-300 py-2 pl-9 pr-3 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 sm:w-72"
            />
          </div>
        </div>

        <div className="divide-y divide-gray-100">
          {loading ? (
            <div className="p-6 text-sm text-gray-500">Loading recipients...</div>
          ) : filteredRecipients.length ? (
            filteredRecipients.map(({ recipient, index }) => {
              const invalid = !EMAIL_PATTERN.test(recipient);
              return (
                <div key={`${recipient}-${index}`} className="grid gap-3 p-3 sm:grid-cols-[3rem_1fr_auto] sm:items-center">
                  <span className="text-xs font-medium text-gray-400">{index + 1}</span>
                  <input
                    type="email"
                    value={recipient}
                    onChange={(event) => updateRecipientAt(index, event.target.value)}
                    className={`min-w-0 rounded-md border px-3 py-2 text-sm focus:outline-none focus:ring-1 ${
                      invalid
                        ? "border-red-300 bg-red-50 focus:border-red-500 focus:ring-red-500"
                        : "border-gray-300 focus:border-blue-500 focus:ring-blue-500"
                    }`}
                  />
                  <button
                    type="button"
                    onClick={() => removeRecipientAt(index)}
                    aria-label={`Remove ${recipient}`}
                    className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-gray-300 text-gray-500 hover:bg-red-50 hover:text-red-600"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              );
            })
          ) : (
            <div className="p-6 text-sm text-gray-500">No recipients match the current search.</div>
          )}
        </div>
      </section>
    </div>
  );
}
