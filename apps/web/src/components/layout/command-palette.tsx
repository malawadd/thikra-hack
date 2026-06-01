"use client";

import { useRouter } from "next/navigation";
import {
  Clapperboard,
  FolderOpen,
  Settings,
  Sparkles,
  FileIcon,
  Moon,
  Sun,
} from "lucide-react";
import { useTheme } from "next-themes";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command";
import { useFiles } from "@/lib/queries";
import { humanizeBytes } from "@/lib/utils";

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const routes = [
  { label: "Studio",        href: "/",         icon: Clapperboard },
  { label: "Files",         href: "/files",    icon: FolderOpen },
  { label: "Settings",      href: "/settings", icon: Settings },
  { label: "Design System", href: "/design",   icon: Sparkles },
];

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const router = useRouter();
  const { setTheme } = useTheme();
  // Lazy via TanStack Query — only fetches when the palette opens.
  const { data: files = [] } = useFiles();

  const runThen = (fn: () => void) => () => {
    onOpenChange(false);
    fn();
  };

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput placeholder="Search assets or jump to a page..." />
      <CommandList>
        <CommandEmpty>No matches found.</CommandEmpty>
        <CommandGroup heading="Navigate">
          {routes.map((r) => (
            <CommandItem
              key={r.href}
              onSelect={runThen(() => router.push(r.href))}
              value={`nav ${r.label}`}
            >
              <r.icon />
              {r.label}
            </CommandItem>
          ))}
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Theme">
          <CommandItem onSelect={runThen(() => setTheme("light"))} value="theme light">
            <Sun />
            Light mode
          </CommandItem>
          <CommandItem onSelect={runThen(() => setTheme("dark"))} value="theme dark">
            <Moon />
            Dark mode
          </CommandItem>
          <CommandItem onSelect={runThen(() => setTheme("system"))} value="theme system">
            <Sparkles />
            System theme
          </CommandItem>
        </CommandGroup>
        {open && files.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Assets">
              {files.slice(0, 20).map((f) => (
                <CommandItem
                  key={f.key}
                  value={`file ${f.display_name ?? f.key}`}
                  onSelect={runThen(() => router.push("/files"))}
                >
                  <FileIcon />
                  <span className="truncate">{f.display_name ?? f.key}</span>
                  <CommandShortcut>{humanizeBytes(f.size)}</CommandShortcut>
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}
      </CommandList>
    </CommandDialog>
  );
}
