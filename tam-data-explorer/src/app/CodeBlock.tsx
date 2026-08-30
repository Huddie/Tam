import { useState } from "react";

type Token = { text: string; cls?: string };

/** A tiny hand-rolled tokenizer, not a real language parser -- good enough
 * to color comments/strings/keywords/placeholders for the handful of
 * short snippets that use this without pulling in a whole syntax-highlighter
 * dependency for something this small. */
function tokenize(code: string, pattern: RegExp, classify: (match: RegExpExecArray) => string): Token[] {
  const tokens: Token[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(code))) {
    if (match.index > last) tokens.push({ text: code.slice(last, match.index) });
    tokens.push({ text: match[0], cls: classify(match) });
    last = match.index + match[0].length;
  }
  if (last < code.length) tokens.push({ text: code.slice(last) });
  return tokens;
}

const BASH_PATTERN = /(#.*$)|("(?:[^"\\]|\\.)*")|(<[a-zA-Z0-9_-]+>)/gm;
const PYTHON_PATTERN =
  /(#.*$)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|\b(import|from|def|return|with|as|True|False|None)\b/gm;

function highlight(code: string, language: "bash" | "python"): Token[] {
  if (language === "bash") {
    return tokenize(code, BASH_PATTERN, (m) => (m[1] ? "tok-comment" : m[2] ? "tok-string" : "tok-placeholder"));
  }
  return tokenize(code, PYTHON_PATTERN, (m) => (m[1] ? "tok-comment" : m[2] ? "tok-string" : "tok-keyword"));
}

export function CodeBlock({ code, language }: { code: string; language: "bash" | "python" }) {
  const [copied, setCopied] = useState(false);
  const tokens = highlight(code, language);

  return (
    <div className="code-block">
      <div className="code-block-header">
        <span className="mono muted">{language}</span>
        <button
          className="code-block-copy"
          onClick={() => {
            navigator.clipboard.writeText(code);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}
        >
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>
      <pre>
        <code>
          {tokens.map((token, i) => (
            <span key={i} className={token.cls}>
              {token.text}
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}
