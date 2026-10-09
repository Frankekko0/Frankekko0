"use client";

import { CircleAlert, CircleCheck, CircleDashed, DatabaseZap, Mail, Upload } from "lucide-react";
import { useRef, type ReactNode } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/feedback";
import { errorMessage } from "@/lib/api";
import { MODE_LABEL, plural, timeAgo } from "@/lib/format";
import { useAcquisitionStatus, useUploadEmails } from "@/lib/queries";
import { cn } from "@/lib/utils";

function Row({ on, title, detail, children }: { on: boolean | "warn"; title: string; detail: ReactNode; children?: ReactNode }) {
  const Icon = on === "warn" ? CircleAlert : on ? CircleCheck : CircleDashed;
  return (
    <li className="flex items-start gap-3 py-3">
      <Icon className={cn("mt-0.5 size-4 shrink-0", on === "warn" ? "text-warning" : on ? "text-success" : "text-fg-3")} aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-[14px] font-medium text-fg">{title}</p>
        <div className="text-[13px] text-fg-2">{detail}</div>
        {children}
      </div>
    </li>
  );
}

/** Transparency page: which acquisition modes are active, what they did, what failed. */
export function DataSourcesCard() {
  const status = useAcquisitionStatus();
  const upload = useUploadEmails();
  const fileRef = useRef<HTMLInputElement>(null);
  const s = status.data;

  async function onFiles(files: FileList | null) {
    if (!files?.length) return;
    upload.mutate(Array.from(files), {
      onSuccess: (r) =>
        toast.success(
          `${plural(r.messages, "email")} read: ${r.sold} sold, ${r.price_drops} price drops${r.ignored ? `, ${r.ignored} not from Vinted or without listings` : ""}`,
        ),
      onError: (e) => toast.error(errorMessage(e)),
    });
    if (fileRef.current) fileRef.current.value = "";
  }

  return (
    <Card className="reveal" id="data-sources">
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2 [&_svg]:size-4 [&_svg]:text-fg-3">
            <DatabaseZap /> Data sources
          </CardTitle>
          <CardDescription>How listings reach FlipFinder. Every record keeps the source it came from.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {!s ? (
          <Skeleton className="h-48 rounded-xl" />
        ) : (
          <>
            <ul className="divide-y divide-line">
              <Row
                on={s.extension.listings > 0}
                title="Browser extension"
                detail={
                  <>
                    {plural(s.extension.listings, "listing")} captured while you browse Vinted
                    {s.extension.last_sync ? ` · last sync ${timeAgo(s.extension.last_sync)}` : ""}. It reads only the pages you open.
                  </>
                }
              />
              <Row
                on={s.provider.listings > 0}
                title="Authorized feed"
                detail={
                  s.provider.name
                    ? `${plural(s.provider.listings, "listing")} from the configured feed, re-checked on an adaptive schedule.`
                    : "Not configured: data come only from what you capture (MARKETPLACE_PROVIDER=feed with FEED_URL to add one)."
                }
              />
              <Row
                on={s.email.enabled ? (s.email.last_error ? "warn" : true) : s.email.listings > 0}
                title="Vinted notification emails"
                detail={
                  s.email.enabled ? (
                    <>
                      Mailbox read every few minutes (read-only){s.email.last_run ? ` · last check ${timeAgo(s.email.last_run)}` : ""}.
                      {s.email.last_error && <span className="text-warning"> {s.email.last_error}</span>}
                    </>
                  ) : (
                    <>
                      &ldquo;Favourite sold&rdquo; and &ldquo;price reduced&rdquo; emails confirm sales and price drops. Upload them here, or set IMAP_HOST /
                      IMAP_USER / IMAP_PASSWORD to read them automatically.
                    </>
                  )
                }
              >
                <input
                  ref={fileRef}
                  type="file"
                  accept=".eml,message/rfc822"
                  multiple
                  className="sr-only"
                  aria-label="Upload Vinted emails (.eml)"
                  onChange={(e) => onFiles(e.target.files)}
                />
                <Button variant="outline" size="sm" className="mt-2" loading={upload.isPending} onClick={() => fileRef.current?.click()}>
                  <Upload /> Upload .eml files
                </Button>
              </Row>
              <Row
                on={Object.values(s.manual).some((n) => n > 0)}
                title="Manual: form, links, search pages, bookmarklet"
                detail={Object.entries(s.manual)
                  .map(([k, n]) => `${MODE_LABEL[k] ?? k}: ${n}`)
                  .join(" · ")}
              />
            </ul>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-fg-3">
              <Badge tone="outline">Parser config {s.parser_config_version}</Badge>
              <span>
                {plural(s.tracked_due_now, "tracked listing")} due for a check (refreshed when you open them on Vinted with the extension)
              </span>
            </div>
            {s.recent_failures.length > 0 && (
              <div className="mt-4">
                <p className="mb-1.5 flex items-center gap-1.5 text-[13px] font-medium text-fg-2">
                  <Mail className="size-3.5" /> Recent failed acquisitions
                </p>
                <ul className="space-y-1 text-[12px]">
                  {s.recent_failures.map((f) => (
                    <li key={f.at + (f.vinted_id ?? "")} className="flex gap-2 text-fg-2">
                      <span className="shrink-0 text-fg-3 tnum">{timeAgo(f.at)}</span>
                      <span className="shrink-0 text-fg-3">{MODE_LABEL[f.mode] ?? f.mode}</span>
                      <span className="min-w-0">
                        {f.vinted_id ? `#${f.vinted_id} · ` : ""}
                        {f.message}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
