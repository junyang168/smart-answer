export interface EmailRecipientsResponse {
  recipients: string[];
  count: number;
  file_path: string;
  production: boolean;
}

const EMAIL_BASE_PATH = "/api/admin/email";

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = await response.text();
    try {
      const parsed = JSON.parse(message);
      message = parsed.detail || message;
    } catch {
      // Keep the plain text response.
    }
    throw new Error(message || `Request failed with status ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function fetchEmailRecipients(): Promise<EmailRecipientsResponse> {
  const response = await fetch(`${EMAIL_BASE_PATH}/recipients`, { cache: "no-store" });
  return parseJson(response);
}

export async function updateEmailRecipients(
  recipients: string[],
): Promise<EmailRecipientsResponse> {
  const response = await fetch(`${EMAIL_BASE_PATH}/recipients`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ recipients }),
  });
  return parseJson(response);
}
