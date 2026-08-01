import { FileBrowser } from "@/components/files/file-browser";

export default function FilesPage() {
  return (
    <div className="space-y-8">
      <div className="animate-fade-in border-b border-border pb-5 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="page-title">Files</h1>
          <p className="text-sm text-muted-foreground mt-1.5">
            Every artifact the pipeline has written to{" "}
            <span className="font-mono text-foreground/80">explainers/</span>{" "}
            on Backblaze B2, grouped by run. Click any file to open the
            presigned playback URL.
          </p>
        </div>
      </div>
      <div className="animate-fade-in-up stagger-2">
        <FileBrowser />
      </div>
    </div>
  );
}
