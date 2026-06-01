"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ApiError,
  createStoryboard,
  getFiles,
  getHealth,
} from "@/lib/api-client";
import type { FileMetadata, HealthResponse, StoryboardResponse } from "@/lib/api-client";

// Single source of truth for query keys. Keep tightly scoped so invalidating
// one bucket doesn't blow away unrelated caches.
export const qk = {
  all: ["genblaze"] as const,
  files: () => [...qk.all, "files"] as const,
  health: () => [...qk.all, "health"] as const,
};

export function useFiles() {
  return useQuery<FileMetadata[], ApiError>({
    queryKey: qk.files(),
    queryFn: getFiles,
  });
}

export function useHealth() {
  return useQuery<HealthResponse, ApiError>({
    queryKey: qk.health(),
    queryFn: getHealth,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

/** Stage A storyboard generation — returns the spec for review/edit. */
export function useCreateStoryboard() {
  return useMutation<StoryboardResponse, ApiError, string>({
    mutationFn: (prompt) => createStoryboard(prompt),
  });
}
