"use client";

import { useMemo, useState } from "react";
import { FolderOpen, FileIcon, ChevronRight, ChevronDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";
import { useFiles } from "@/lib/queries";
import { buildFileTree, type TreeNode } from "@/lib/file-tree";
import { humanizeBytes } from "@/lib/utils";
import { API_BASE } from "@/lib/api-client";

/**
 * Tree-style browser over /files. Folders are collapsible; files link to
 * `/assets/{key}` which the backend 302-redirects to a presigned URL.
 */
export function FileBrowser() {
  const { data: files = [], isLoading, error, refetch } = useFiles();
  const tree = useMemo(() => buildFileTree(files), [files]);
  const [open, setOpen] = useState<Set<string>>(() => new Set<string>(["explainers/"]));

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    );
  }

  if (error) {
    return (
      <ErrorState
        error={error}
        title="Couldn't load assets"
        description={error.message}
        onRetry={() => refetch()}
      />
    );
  }

  if (tree.length === 0) {
    return (
      <EmptyState
        icon={FolderOpen}
        title="No assets yet"
        description="Generate an explainer from the Studio page to populate this bucket."
      />
    );
  }

  const toggle = (path: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  return (
    <div className="rounded-md border border-border bg-card">
      <ul className="text-sm divide-y divide-border">
        {tree.map((node) => (
          <FileNode key={nodeKey(node)} node={node} depth={0} open={open} onToggle={toggle} />
        ))}
      </ul>
    </div>
  );
}

function FileNode({
  node, depth, open, onToggle,
}: {
  node: TreeNode;
  depth: number;
  open: Set<string>;
  onToggle: (path: string) => void;
}) {
  if (node.type === "folder") {
    const isOpen = open.has(node.path);
    return (
      <li>
        <button
          onClick={() => onToggle(node.path)}
          className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/40 transition-colors"
          style={{ paddingLeft: `${depth * 16 + 12}px` }}
        >
          {isOpen
            ? <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
            : <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />}
          <FolderOpen className="h-4 w-4 text-muted-foreground shrink-0" />
          <span className="font-medium truncate">{node.name}</span>
          <Badge variant="outline" className="ml-auto shrink-0 font-mono text-[10px]">
            {childCount(node)}
          </Badge>
        </button>
        {isOpen && (
          <ul className="divide-y divide-border">
            {node.children.map((child) => (
              <FileNode
                key={nodeKey(child)} node={child} depth={depth + 1}
                open={open} onToggle={onToggle}
              />
            ))}
          </ul>
        )}
      </li>
    );
  }

  return (
    <li>
      <a
        href={`${API_BASE}/assets/${node.data.key}`}
        target="_blank"
        rel="noreferrer"
        className="flex items-center gap-2 px-3 py-2 hover:bg-muted/40 transition-colors"
        style={{ paddingLeft: `${depth * 16 + 12}px` }}
      >
        <span className="w-3.5 shrink-0" />
        <FileIcon className="h-4 w-4 text-muted-foreground shrink-0" />
        <span className="font-mono text-xs truncate" title={node.data.key}>
          {node.name}
        </span>
        <Badge variant="outline" className="ml-auto shrink-0 font-mono text-[10px]">
          {humanizeBytes(node.data.size)}
        </Badge>
      </a>
    </li>
  );
}

function childCount(folder: { children: TreeNode[] }): number {
  return folder.children.reduce(
    (n, c) => n + (c.type === "folder" ? childCount(c) : 1),
    0,
  );
}

function nodeKey(node: TreeNode): string {
  return node.type === "folder" ? `dir:${node.path}` : `file:${node.data.key}`;
}
