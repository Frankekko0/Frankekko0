"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { useUnreadCount } from "@/lib/queries";

/** Surfaces new high-priority alerts while the app is open: toast + system notification. */
export function AlertsWatcher() {
  const { data } = useUnreadCount();
  const router = useRouter();
  const seen = useRef<string | null>(null);
  const initialized = useRef(false);

  useEffect(() => {
    if ("serviceWorker" in navigator && process.env.NODE_ENV === "production") {
      navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    const latest = data?.latest_high_priority;
    if (!data) return;
    if (!initialized.current) {
      initialized.current = true;
      seen.current = latest?.id ?? null;
      return;
    }
    if (!latest || latest.id === seen.current) return;
    seen.current = latest.id;
    const url = latest.opportunity_id ? `/deals/${latest.opportunity_id}` : "/alerts";
    toast(latest.title, {
      description: latest.body,
      duration: 12_000,
      action: { label: "Open", onClick: () => router.push(url) },
    });
    if (typeof Notification !== "undefined" && Notification.permission === "granted" && document.visibilityState === "hidden") {
      const n = new Notification(latest.title, { body: latest.body, icon: "/icons/icon-192.png", tag: latest.id });
      n.onclick = () => {
        window.focus();
        router.push(url);
      };
    }
  }, [data, router]);

  return null;
}
