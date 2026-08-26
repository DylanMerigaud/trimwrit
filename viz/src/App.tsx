import { useCallback, useEffect, useMemo, useState } from "react";

import { PipelineCanvas } from "./components/pipeline/pipeline-canvas";
import { decodeHashPayload, parsePipelineDoc, PayloadDecodeError } from "./lib/decode";
import { collectGraphWarnings } from "./lib/graph";
import { exampleDoc } from "./lib/example-doc";
import type { PipelineDoc } from "./lib/types";

type Tab = "canvas" | "paste";

const LEGEND: { status: string; label: string; dot: string }[] = [
  { status: "pending", label: "Pending", dot: "bg-amber-500" },
  { status: "passed", label: "Passed", dot: "bg-emerald-500" },
  { status: "failed", label: "Failed", dot: "bg-red-500" },
  { status: "skipped", label: "Skipped", dot: "bg-muted-foreground" },
];

const readHash = (): string => window.location.hash;

export default function App() {
  const [doc, setDoc] = useState<PipelineDoc>(exampleDoc);
  const [source, setSource] = useState<"example" | "hash" | "paste">("example");
  const [hashError, setHashError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("canvas");
  const [pasteText, setPasteText] = useState("");
  const [pasteError, setPasteError] = useState<string | null>(null);

  const loadFromHash = useCallback(async () => {
    const hash = readHash();
    if (!hash || hash === "#") {
      setSource("example");
      setDoc(exampleDoc);
      setHashError(null);
      return;
    }
    try {
      const decoded = await decodeHashPayload(hash);
      setDoc(decoded);
      setSource("hash");
      setHashError(null);
    } catch (err) {
      const message = err instanceof PayloadDecodeError ? err.message : "could not read the payload";
      setHashError(message);
      // Never a blank page: keep whatever last rendered (starts as the example).
    }
  }, []);

  useEffect(() => {
    void loadFromHash();
    const onHashChange = () => void loadFromHash();
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [loadFromHash]);

  const applyPaste = useCallback(() => {
    try {
      const parsed = parsePipelineDoc(pasteText);
      setDoc(parsed);
      setSource("paste");
      setPasteError(null);
      setTab("canvas");
    } catch (err) {
      setPasteError(err instanceof PayloadDecodeError ? err.message : "could not parse this JSON");
    }
  }, [pasteText]);

  const warnings = useMemo(() => collectGraphWarnings(doc), [doc]);

  return (
    <div className="bg-background text-foreground flex h-screen w-screen flex-col">
      <header className="border-border flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
        <div className="min-w-0">
          <h1 className="truncate text-sm font-semibold" title={doc.name}>
            trimwrit viz &middot; {doc.name}
          </h1>
          <p className="text-muted-foreground text-xs">
            generated {doc.generated_at}
            {source === "example" && " (bundled example, no #v1: payload in the URL)"}
            {source === "paste" && " (pasted document)"}
          </p>
        </div>

        <div className="flex items-center gap-4">
          <ul className="flex items-center gap-3 text-xs" aria-label="status legend">
            {LEGEND.map((entry) => (
              <li key={entry.status} className="flex items-center gap-1.5">
                <span className={`size-2 rounded-full ${entry.dot}`} aria-hidden="true" />
                <span className="text-muted-foreground">{entry.label}</span>
              </li>
            ))}
          </ul>

          <div className="border-border flex overflow-hidden rounded-lg border text-xs">
            <button
              type="button"
              onClick={() => setTab("canvas")}
              className={`px-3 py-1.5 ${tab === "canvas" ? "bg-secondary text-secondary-foreground" : "text-muted-foreground"}`}
            >
              Canvas
            </button>
            <button
              type="button"
              onClick={() => setTab("paste")}
              className={`px-3 py-1.5 ${tab === "paste" ? "bg-secondary text-secondary-foreground" : "text-muted-foreground"}`}
            >
              Paste JSON
            </button>
          </div>
        </div>
      </header>

      {hashError && (
        <div className="border-destructive/40 bg-destructive/10 text-destructive border-b px-4 py-2 text-xs">
          Could not read the #v1: payload in the URL: {hashError}. Showing the last valid document instead.
        </div>
      )}

      {warnings.length > 0 && (
        <div className="border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-xs text-amber-700 dark:text-amber-400">
          <p className="font-medium">This document has {warnings.length} graph warning(s), rendering the rest anyway:</p>
          <ul className="list-inside list-disc">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      <main className="min-h-0 flex-1">
        {tab === "canvas" ? (
          <PipelineCanvas doc={doc} className="h-full w-full" />
        ) : (
          <div className="flex h-full flex-col gap-3 p-4">
            <label htmlFor="paste-json" className="text-sm font-medium">
              Paste a raw (uncompressed) pipeline document
            </label>
            <textarea
              id="paste-json"
              value={pasteText}
              onChange={(event) => setPasteText(event.target.value)}
              placeholder={'{"v": 1, "name": "...", "generated_at": "...", "stages": [...], "roots": [...]}'}
              className="border-border bg-card text-card-foreground min-h-0 flex-1 rounded-lg border p-3 font-mono text-xs"
              spellCheck={false}
            />
            {pasteError && (
              <p className="text-destructive text-xs">{pasteError}</p>
            )}
            <div>
              <button
                type="button"
                onClick={applyPaste}
                className="bg-secondary text-secondary-foreground rounded-lg px-4 py-2 text-sm font-medium"
              >
                Apply
              </button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
