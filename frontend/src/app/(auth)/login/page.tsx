import { Suspense } from "react";
import { AuthForm } from "@/components/forms/auth-form";

export const metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <Suspense>
      <AuthForm mode="login" />
    </Suspense>
  );
}
