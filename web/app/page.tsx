"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type State = { code: string; name: string };
type Source = { name: string; category: string; website: string | null };
type Message =
  | { role: "user"; text: string }
  | { role: "bot"; text: string; sources: Source[] }
  | { role: "error"; text: string };

const QUICK_REPLIES = [
  "Which elder law attorneys can help with Medicaid planning?",
  "What home care agencies are available?",
  "Where can I find memory care options?",
  "Who can help with veterans benefits?",
];

function safeHref(url: string): string {
  return /^https?:\/\//i.test(url) ? url : `https://${url}`;
}

// Renders the small Markdown subset the API produces: paragraphs, "- " bullet lists and **bold**.
function Inline({ text }: { text: string }) {
  return (
    <>
      {text.split(/\*\*(.+?)\*\*/g).map((part, i) =>
        i % 2 === 1 ? <strong key={i}>{part}</strong> : <span key={i}>{part}</span>,
      )}
    </>
  );
}

function FormattedText({ text }: { text: string }) {
  const blocks: ({ type: "p"; text: string } | { type: "ul"; items: string[] })[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const bullet = line.match(/^[-*•]\s+(.*)$/);
    const last = blocks[blocks.length - 1];
    if (bullet) {
      if (last?.type === "ul") last.items.push(bullet[1]);
      else blocks.push({ type: "ul", items: [bullet[1]] });
    } else {
      blocks.push({ type: "p", text: line });
    }
  }
  return (
    <div className="space-y-2 leading-relaxed">
      {blocks.map((b, i) =>
        b.type === "ul" ? (
          <ul key={i} className="list-disc space-y-1.5 pl-5">
            {b.items.map((item, j) => (
              <li key={j}>
                <Inline text={item} />
              </li>
            ))}
          </ul>
        ) : (
          <p key={i}>
            <Inline text={b.text} />
          </p>
        ),
      )}
    </div>
  );
}

export default function Home() {
  const [states, setStates] = useState<State[]>([]);
  const [state, setState] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`${API}/api/states`)
      .then((r) => r.json())
      .then((s: State[]) => {
        setStates(s);
        if (s.length) setState(s[0].code);
      })
      .catch(() => setApiError(`Can't reach the API at ${API}. Is it running?`));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function ask(raw: string) {
    const q = raw.trim();
    if (q.length < 3 || !state || loading) return;
    setQuestion("");
    setMessages((m) => [...m, { role: "user", text: q }]);
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state, question: q }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`);
      const data = await res.json();
      setMessages((m) => [...m, { role: "bot", text: data.answer, sources: data.sources ?? [] }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "error", text: String(err instanceof Error ? err.message : err) }]);
    } finally {
      setLoading(false);
    }
  }

  function send(e: FormEvent) {
    e.preventDefault();
    ask(question);
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-200 sm:p-6">
      <section className="flex h-screen w-full max-w-[467px] flex-col overflow-hidden bg-[#f7f9fb] shadow-2xl sm:h-[687px] sm:rounded-2xl">
        {/* Header */}
        <header className="flex items-center gap-3 bg-gradient-to-r from-[#0e7490] to-[#0b5568] px-5 py-4 text-white">
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[17px] font-semibold leading-tight">Senior Care Resource Finder</h1>
            <p className="truncate text-sm text-white/80">Referral partners by state</p>
          </div>
          <button
            type="button"
            onClick={() => setMessages([])}
            aria-label="Reset chat"
            className="rounded-full p-1.5 text-white/90 transition hover:bg-white/15"
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </header>

        {/* State selector (the API needs a state to search) */}
        {states.length > 0 && (
          <div className="flex items-center gap-2 border-b border-slate-200 bg-white px-5 py-2 text-xs text-slate-500">
            <span>Location</span>
            <select
              value={state}
              onChange={(e) => setState(e.target.value)}
              className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 outline-none focus:border-teal-600"
            >
              {states.map((s) => (
                <option key={s.code} value={s.code}>
                  {s.code} – {s.name}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Messages */}
        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5 text-[15px] text-slate-700">
          <div className="flex items-center gap-3 text-xs font-semibold tracking-wider text-slate-400">
            <span className="h-px flex-1 bg-slate-200" />
            TODAY
            <span className="h-px flex-1 bg-slate-200" />
          </div>

          <div className="flex items-end gap-2.5">
            <div className="max-w-[85%] rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-3 text-[17px] leading-snug text-slate-800 shadow-sm">
              Hi there <span aria-hidden>👋</span> How can I assist you today?
            </div>
          </div>

          {messages.length === 0 && (
            <div className="flex flex-col items-end gap-2.5">
              {QUICK_REPLIES.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => ask(q)}
                  disabled={!state}
                  className="rounded-full border border-teal-200 bg-white px-4 py-2 text-[15px] font-medium text-teal-700 transition hover:border-teal-600 hover:bg-teal-50 disabled:opacity-50"
                >
                  {q}
                </button>
              ))}
            </div>
          )}

          {apiError && (
            <div className="rounded-lg border border-red-300 bg-red-50 px-4 py-2.5 text-sm text-red-600">{apiError}</div>
          )}

          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="max-w-[80%] rounded-2xl rounded-br-md bg-[#0e7490] px-4 py-2.5 text-white shadow-sm">
                  {m.text}
                </div>
              </div>
            ) : m.role === "error" ? (
              <div key={i} className="rounded-lg border border-red-300 bg-red-50 px-4 py-2.5 text-sm text-red-600">
                {m.text}
              </div>
            ) : (
              <div key={i} className="flex items-end gap-2.5">
                    <div className="max-w-[85%] rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-3 shadow-sm">
                  <FormattedText text={m.text} />
                  {m.sources.length > 0 && (
                    <div className="mt-3 border-t border-slate-200 pt-2 text-sm">
                      <p className="font-medium">Sources:</p>
                      <ol className="mt-1 list-inside list-decimal space-y-0.5">
                        {m.sources.map((s, j) => (
                          <li key={j}>
                            {s.website ? (
                              <a
                                href={safeHref(s.website)}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-teal-700 underline"
                              >
                                {s.name}
                              </a>
                            ) : (
                              s.name
                            )}{" "}
                            <span className="text-slate-400">({s.category})</span>
                          </li>
                        ))}
                      </ol>
                    </div>
                  )}
                </div>
              </div>
            ),
          )}

          {loading && (
            <div className="flex items-end gap-2.5">
                <div className="flex gap-1 rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-3.5 shadow-sm">
                {[0, 150, 300].map((d) => (
                  <span
                    key={d}
                    className="h-2 w-2 animate-bounce rounded-full bg-slate-400"
                    style={{ animationDelay: `${d}ms` }}
                  />
                ))}
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>

        {/* Input */}
        <form onSubmit={send} className="border-t border-slate-300 bg-white px-4 py-3">
          <div className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-white py-2 pl-4 pr-2 focus-within:border-teal-600">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Type your question.."
              className="min-w-0 flex-1 bg-transparent text-[15px] text-slate-800 outline-none placeholder:text-slate-400"
            />
            <button
              type="submit"
              disabled={loading || question.trim().length < 3 || !state}
              aria-label="Send"
              className="flex h-11 w-11 items-center justify-center rounded-xl bg-[#0e7490] text-white transition hover:bg-[#0b5568] disabled:opacity-40"
            >
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            </button>
          </div>
        </form>
      </section>
    </main>
  );
}
