"use client";

import { Bookmark, ExternalLink, Link2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/input";
import { errorMessage } from "@/lib/api";
import { useImportLinks } from "@/lib/queries";
import type { LinkImportResult } from "@/lib/types";

/** Paste one link or a whole list: each Vinted listing becomes a tracked record. */
export function PasteLinksCard({ index = 0 }: { index?: number }) {
  const [text, setText] = useState("");
  const [result, setResult] = useState<LinkImportResult | null>(null);
  const importer = useImportLinks();
  return (
    <Card className="enter p-5" style={{ "--i": index } as CSSProperties}>
      <div className="flex items-center gap-2 text-[14px] font-semibold">
        <Link2 className="size-4 text-accent" /> Track links
      </div>
      <p className="mt-1 text-[13px] text-fg-2">
        Paste one or more Vinted links (a list, a chat message, anything). Each becomes a tracked record in your Archive; its details are filled in
        when it is read (open it with the extension, or the optional server read).
      </p>
      <Textarea
        className="mt-3"
        rows={4}
        aria-label="Vinted links"
        placeholder={"https://www.vinted.it/items/…\nhttps://www.vinted.fr/items/…"}
        value={text}
        onChange={(e) => setText(e.target.value)}
        maxLength={100_000}
      />
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-xs text-fg-3">Duplicates and non-Vinted links are skipped.</span>
        <Button
          size="sm"
          loading={importer.isPending}
          disabled={!text.trim()}
          onClick={() =>
            importer.mutate(text, {
              onSuccess: (r) => {
                setResult(r);
                if (r.found) {
                  toast.success(`${r.created} new, ${r.existing} already known: all tracked`);
                  setText("");
                } else toast.error(r.message ?? "No Vinted listing links found.");
              },
              onError: (e) => toast.error(errorMessage(e)),
            })
          }
        >
          Track {text.trim() ? "" : "links"}
        </Button>
      </div>
      {result && result.found > 0 && (
        <div className="mt-3 rounded-xl bg-surface-2 p-3 text-[13px]">
          <p className="font-medium text-fg">
            {result.found} listing{result.found === 1 ? "" : "s"}: {result.created} new · {result.existing} already in your Archive
          </p>
          <ul className="mt-1.5 max-h-40 space-y-1 overflow-y-auto">
            {result.items.map((i) => (
              <li key={i.vinted_id} className="flex items-center justify-between gap-2">
                <span className="tnum text-fg-2">#{i.vinted_id}</span>
                <span className="flex gap-3">
                  <Link href={`/items/${i.listing_id}`} className="text-accent hover:underline">
                    Tracking page
                  </Link>
                  <a href={i.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-fg-2 hover:text-fg">
                    Vinted <ExternalLink className="size-3" />
                  </a>
                </span>
              </li>
            ))}
          </ul>
          {!result.public_fetch_enabled && (
            <p className="mt-2 text-xs text-fg-3">Open them on Vinted with the extension installed to fill in price, photos and status.</p>
          )}
        </div>
      )}
    </Card>
  );
}

/** Bookmarklet: reads the structured data of the Vinted listing you are viewing and opens it here. */
export function bookmarkletCode(appOrigin: string): string {
  const app = JSON.stringify(appOrigin.replace(/\/+$/, ""));
  return (
    "javascript:(()=>{let p=null;for(const s of document.querySelectorAll('script[type=\"application/ld+json\"]')){try{const j=JSON.parse(s.textContent);" +
    "for(const n of [].concat(j['@graph']||j)){if(/product/i.test(String(n&&n['@type'])))p=p||n}}catch(e){}}" +
    "const m=k=>(document.querySelector('meta[property=\"'+k+'\"]')||{}).content;const o=p?[].concat(p.offers||[])[0]||{}:{};" +
    "const t=((p&&p.name)||m('og:title')||document.title||'').replace(/\\s*[|\\-]\\s*Vinted\\s*$/i,'');" +
    "const d={v:1,source:'bookmarklet',url:location.origin+location.pathname,title:t,price:Number(o.price||m('product:price:amount'))||null," +
    "brand:p&&p.brand?(p.brand.name||p.brand):'',description:(p&&p.description)||'',image_urls:[].concat((p&&p.image)||m('og:image')||[]).slice(0,20)};" +
    "const b=btoa(unescape(encodeURIComponent(JSON.stringify(d)))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');" +
    `window.open(${app}+'/analyze#import='+b,'_blank')})()`
  );
}

export function BookmarkletCard({ index = 0 }: { index?: number }) {
  const ref = useRef<HTMLAnchorElement>(null);
  useEffect(() => {
    // Set directly on the element: React deliberately refuses javascript: URLs in JSX.
    ref.current?.setAttribute("href", bookmarkletCode(window.location.origin));
  }, []);
  return (
    <Card className="enter p-5" style={{ "--i": index } as CSSProperties}>
      <div className="flex items-center gap-2 text-[14px] font-semibold">
        <Bookmark className="size-4 text-accent" /> Bookmarklet
      </div>
      <p className="mt-1 text-[13px] text-fg-2">
        No extension (another browser, a work computer)? Drag this button to your bookmarks bar. On a Vinted listing, click it: the listing opens here
        with its details filled in.
      </p>
      <a
        ref={ref}
        className="mt-3 inline-flex cursor-grab items-center gap-2 rounded-xl border border-dashed border-accent/50 bg-accent-soft px-3 py-2 text-[13px] font-semibold text-accent active:cursor-grabbing"
        onClick={(e) => {
          e.preventDefault();
          toast.message("Drag it to your bookmarks bar, then use it on a Vinted listing.");
        }}
      >
        <Bookmark className="size-4" /> FlipFinder
      </a>
      <p className="mt-2 text-xs text-fg-3">It reads only the page you are on, when you click it. Nothing is sent anywhere but FlipFinder.</p>
    </Card>
  );
}
