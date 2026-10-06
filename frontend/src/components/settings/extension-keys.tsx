"use client";

import { Check, Copy, KeyRound, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/feedback";
import { errorMessage } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { useCreateExtensionKey, useExtensionKeys, useRevokeExtensionKey } from "@/lib/queries";

/** Pairing of the browser extension: a revocable FlipFinder key, shown once. */
export function ExtensionKeysCard() {
  const keys = useExtensionKeys();
  const create = useCreateExtensionKey();
  const revoke = useRevokeExtensionKey();
  const [fresh, setFresh] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const active = (keys.data ?? []).filter((k) => !k.revoked_at);

  async function copy() {
    if (!fresh) return;
    try {
      await navigator.clipboard.writeText(fresh);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Copy failed: select the key and copy it by hand.");
    }
  }

  return (
    <Card className="reveal" id="extension">
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2 [&_svg]:size-4 [&_svg]:text-fg-3">
            <KeyRound /> Browser extension
          </CardTitle>
          <CardDescription>
            The extension sends what you see on Vinted to FlipFinder with its own key. Your Vinted login, cookies and tokens are never read or sent.
          </CardDescription>
        </div>
        <Button
          size="sm"
          loading={create.isPending}
          onClick={() =>
            create.mutate("Browser extension", {
              onSuccess: (k) => setFresh(k.key),
              onError: (e) => toast.error(errorMessage(e)),
            })
          }
        >
          <Plus /> New key
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {fresh && (
          <div className="rounded-xl border border-accent/40 bg-accent-soft p-3.5">
            <p className="text-[13px] font-medium text-fg">Paste this key in the extension options (it is shown only now):</p>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 select-all truncate rounded-lg bg-surface px-2.5 py-2 font-mono text-[12px] text-fg">{fresh}</code>
              <Button variant="outline" size="sm" onClick={copy} aria-label="Copy key">
                {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy"}
              </Button>
            </div>
            <p className="mt-2 text-xs text-fg-3">
              Extension options → FlipFinder address: <span className="font-mono">{typeof window !== "undefined" ? window.location.origin : ""}</span>
            </p>
          </div>
        )}
        {!keys.data ? (
          <Skeleton className="h-16 rounded-xl" />
        ) : keys.data.length === 0 ? (
          <p className="text-[13px] text-fg-2">No key yet. Create one, then paste it in the extension options to turn on badges, the live panel and automatic sync.</p>
        ) : (
          <ul className="divide-y divide-line">
            {keys.data.map((k) => (
              <li key={k.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 text-[13px]">
                <span className="font-mono text-fg">{k.prefix}…</span>
                <span className="text-fg-2">{k.name}</span>
                {k.revoked_at ? (
                  <Badge tone="neutral">Revoked {timeAgo(k.revoked_at)}</Badge>
                ) : (
                  <Badge tone="success">{k.last_used_at ? `Used ${timeAgo(k.last_used_at)}` : "Never used"}</Badge>
                )}
                <span className="text-xs text-fg-3">created {timeAgo(k.created_at)}</span>
                {!k.revoked_at && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="ml-auto"
                    loading={revoke.isPending && revoke.variables === k.id}
                    onClick={() =>
                      revoke.mutate(k.id, {
                        onSuccess: () => toast.success("Key revoked: the extension using it stops syncing."),
                        onError: (e) => toast.error(errorMessage(e)),
                      })
                    }
                  >
                    <Trash2 /> Revoke
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {active.length > 1 && <p className="text-xs text-fg-3">One key per browser keeps revoking simple.</p>}
      </CardContent>
    </Card>
  );
}
