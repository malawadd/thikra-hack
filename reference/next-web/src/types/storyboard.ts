// Mirrors `services/api/app/types/storyboard.py` — kept aligned by convention
// (the backend ships the JSON schema via response_format; the frontend never
// constructs a spec from scratch, only displays + edits one).

export type Scene = {
  image_prompt: string;
  motion_prompt: string;
  narration: string;
  caption: string;
  duration_sec: number;
};

export type StoryboardSpec = {
  title: string;
  // Visual style guide locked once by Stage A. Stage B0 renders a single
  // reference image from this; Stage B1 prefixes it onto every per-scene
  // prompt so all keyframes share a look.
  style_prompt: string;
  music_prompt: string;
  total_duration_sec: number;
  scenes: Scene[];
};
