import type { FileMetadata } from "@/lib/api-client";

export interface TreeFolder {
  type: "folder";
  name: string;
  path: string;
  children: TreeNode[];
}

export interface TreeFile {
  type: "file";
  name: string;
  data: FileMetadata;
}

export type TreeNode = TreeFolder | TreeFile;

/**
 * Build a tree structure from a flat list of S3 keys.
 * e.g. ["explainers/run-abc/keyframe.png", "explainers/run-abc/clips/scene-1.mp4"]
 * becomes a nested folder/file hierarchy.
 */
export function buildFileTree(files: FileMetadata[]): TreeNode[] {
  const root: TreeFolder = {
    type: "folder",
    name: "",
    path: "",
    children: [],
  };

  for (const file of files) {
    const parts = file.key.split("/");
    let current = root;

    // Walk/create folders for all parts except the last (filename).
    for (let i = 0; i < parts.length - 1; i++) {
      const folderName = parts[i];
      const folderPath = parts.slice(0, i + 1).join("/") + "/";
      let folder = current.children.find(
        (c): c is TreeFolder => c.type === "folder" && c.name === folderName,
      );
      if (!folder) {
        folder = {
          type: "folder",
          name: folderName,
          path: folderPath,
          children: [],
        };
        current.children.push(folder);
      }
      current = folder;
    }

    // Add the file as a leaf — fall back to the full key if no filename.
    const leafName = parts[parts.length - 1] || file.key;
    current.children.push({ type: "file", name: leafName, data: file });
  }

  sortTree(root.children);
  return root.children;
}

function sortTree(nodes: TreeNode[]) {
  nodes.sort((a, b) => {
    if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
    if (a.type === "folder" && b.type === "folder") {
      return a.name.localeCompare(b.name);
    }
    if (a.type === "file" && b.type === "file") {
      const ta = a.data.last_modified ? new Date(a.data.last_modified).getTime() : 0;
      const tb = b.data.last_modified ? new Date(b.data.last_modified).getTime() : 0;
      return tb - ta; // Most recent first.
    }
    return 0;
  });

  for (const node of nodes) {
    if (node.type === "folder") sortTree(node.children);
  }
}
