type History = { clearHistory: () => void };

export const reviewEditorHistoryEvents = {
  change(editor: History, change: { origin?: string }) {
    // The Markdown wrapper uses setValue when it loads text from the server.
    if (change.origin === "setValue") editor.clearHistory();
  },
};
