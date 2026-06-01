"use client";

// Default flow now opens the storyboard expanded so users see scenes
// without an extra click. Per-scene accordion is collapsed by default —
// users open the scenes they actually want to refine.

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import type { Scene, StoryboardSpec } from "@/types/storyboard";

export function StoryboardReview({
  spec,
  onChange,
  disabled,
}: {
  spec: StoryboardSpec;
  onChange: (next: StoryboardSpec) => void;
  disabled?: boolean;
}) {
  const update = (i: number, patch: Partial<Scene>) => {
    const next = { ...spec, scenes: spec.scenes.map((s, idx) => (idx === i ? { ...s, ...patch } : s)) };
    onChange(next);
  };

  return (
    <Accordion type="single" collapsible defaultValue="review">
      <AccordionItem value="review">
        <AccordionTrigger>
          Review &amp; refine storyboard ({spec.scenes.length} scenes)
        </AccordionTrigger>
        <AccordionContent>
          <div className="space-y-4">
            <div className="grid gap-2">
              <Label htmlFor="title">Title</Label>
              <Input
                id="title"
                value={spec.title}
                onChange={(e) => onChange({ ...spec, title: e.target.value })}
                disabled={disabled}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="style">Visual style</Label>
              <Textarea
                id="style"
                value={spec.style_prompt}
                onChange={(e) => onChange({ ...spec, style_prompt: e.target.value })}
                rows={2}
                disabled={disabled}
              />
              <p className="text-xs text-muted-foreground">
                Rendered as Stage B0&apos;s reference image AND prefixed onto every
                per-scene prompt so all keyframes share a look.
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="music">Music prompt</Label>
              <Input
                id="music"
                value={spec.music_prompt}
                onChange={(e) => onChange({ ...spec, music_prompt: e.target.value })}
                disabled={disabled}
              />
            </div>
            <Accordion type="multiple" className="rounded-md border">
              {spec.scenes.map((scene, i) => (
                <AccordionItem key={i} value={`scene-${i}`}>
                  <AccordionTrigger className="px-3">
                    Scene {i + 1} — {scene.caption || "(no caption)"} · {scene.duration_sec}s
                  </AccordionTrigger>
                  <AccordionContent>
                    <div className="space-y-2 px-3">
                      <Label htmlFor={`img-${i}`}>Image prompt</Label>
                      <Textarea
                        id={`img-${i}`}
                        value={scene.image_prompt}
                        onChange={(e) => update(i, { image_prompt: e.target.value })}
                        rows={2}
                        disabled={disabled}
                      />
                      <Label htmlFor={`mot-${i}`}>Motion prompt</Label>
                      <Textarea
                        id={`mot-${i}`}
                        value={scene.motion_prompt}
                        onChange={(e) => update(i, { motion_prompt: e.target.value })}
                        rows={2}
                        disabled={disabled}
                      />
                      <Label htmlFor={`narr-${i}`}>Narration</Label>
                      <Textarea
                        id={`narr-${i}`}
                        value={scene.narration}
                        onChange={(e) => update(i, { narration: e.target.value })}
                        rows={2}
                        disabled={disabled}
                      />
                      <Label htmlFor={`cap-${i}`}>Caption</Label>
                      <Input
                        id={`cap-${i}`}
                        value={scene.caption}
                        onChange={(e) => update(i, { caption: e.target.value })}
                        disabled={disabled}
                      />
                    </div>
                  </AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
          </div>
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  );
}
