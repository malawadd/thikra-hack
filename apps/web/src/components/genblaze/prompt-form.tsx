"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";

const EXAMPLES = [
  "A kid's introduction to how solar panels work",
  "Why ocean currents drive global weather, for a curious 10-year-old",
  "The story of how vaccines train the immune system",
];

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
          onChange={(e) => setValue(e.target.value)}
          placeholder="A kid's introduction to how solar panels work"
          rows={3}
          disabled={disabled}
          required
        />
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
