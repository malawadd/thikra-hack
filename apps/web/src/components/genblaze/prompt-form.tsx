"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

const EXAMPLES = [
  "How large language models predict the next token, for a new product manager",
  "What RAG is and why it grounds LLM answers, for new engineers",
  "How diffusion models turn noise into images, for a curious designer",
];

// Mirror of the server-side `_PROMPT_MAX` in services/api/app/types/api.py.
// Enforced client-side so a long brief gets a visible counter + hard cap
// rather than a silent 422 from the backend's request validation.
const PROMPT_MAX = 2000;

export function PromptForm({
  onSubmit,
  disabled,
}: {
  onSubmit: (prompt: string) => void;
  disabled?: boolean;
}) {
  const [value, setValue] = useState("");

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) onSubmit(value.trim());
      }}
    >
      <div className="space-y-2">
        <Label htmlFor="prompt">Explainer topic</Label>
        <Textarea
          id="prompt"
          value={value}
          onChange={(e) => setValue(e.target.value.slice(0, PROMPT_MAX))}
          placeholder="How large language models predict the next token, for a new product manager"
          rows={3}
          maxLength={PROMPT_MAX}
          disabled={disabled}
          required
        />
        <p className={cn(
          "text-right text-[11px] tabular-nums",
          value.length >= PROMPT_MAX ? "text-destructive" : "text-muted-foreground",
        )}>
          {value.length} / {PROMPT_MAX}
        </p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            type="button"
            className="prompt-chip"
            onClick={() => setValue(ex)}
            disabled={disabled}
          >
            {ex}
          </button>
        ))}
      </div>
      <div className="flex justify-end">
        <Button type="submit" disabled={disabled || !value.trim()}>
          {disabled ? "Generating…" : "Generate explainer"}
        </Button>
      </div>
    </form>
  );
}
