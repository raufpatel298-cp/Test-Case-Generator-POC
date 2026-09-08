"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { supabase } from "../lib/supabase";

export default function ResetPasswordPage() {
  const router = useRouter();

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [ready, setReady] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let mounted = true;

    async function checkSession() {
      const { data } = await supabase.auth.getSession();

      if (mounted) {
        setReady(Boolean(data.session));
      }
    }

    checkSession();

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((event, session) => {
      if (!mounted) return;

      if (event === "PASSWORD_RECOVERY") {
        setReady(true);
        return;
      }

      if (session) {
        setReady(true);
      }
    });

    return () => {
      mounted = false;
      subscription.unsubscribe();
    };
  }, []);

  async function handleUpdatePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    setError("");
    setMessage("");

    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }

    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setLoading(true);

    const { error } = await supabase.auth.updateUser({
      password,
    });

    if (error) {
      setError(error.message);
      setLoading(false);
      return;
    }

    setMessage("Your password has been updated successfully.");

    setPassword("");
    setConfirmPassword("");
    setLoading(false);

    setTimeout(() => {
      router.replace("/login");
    }, 1500);
  }

  if (!ready) {
    return (
      <main className="auth-page">
        <section className="auth-card">
          <div className="auth-brand">
            <div className="auth-brand-mark">✦</div>

            <div>
              <div className="auth-brand-main">AI Testing</div>
              <div className="auth-brand-sub">TOOLS</div>
            </div>
          </div>

          <div className="auth-heading">
            <h1>Reset Password</h1>
            <p>Open the password reset link from your email to continue.</p>
          </div>

          <div className="auth-signup">
            <Link href="/login">Back to Sign In</Link>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="auth-page">
      <section className="auth-card">
        <div className="auth-brand">
          <div className="auth-brand-mark">✦</div>

          <div>
            <div className="auth-brand-main">AI Testing</div>
            <div className="auth-brand-sub">TOOLS</div>
          </div>
        </div>

        <div className="auth-heading">
          <h1>Reset Password</h1>
          <p>Choose a new password for your account</p>
        </div>

        <form onSubmit={handleUpdatePassword} className="auth-form">
          <div className="auth-field">
            <label htmlFor="new-password">New Password</label>

            <input
              id="new-password"
              type="password"
              placeholder="At least 8 characters"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              minLength={8}
              autoComplete="new-password"
            />
          </div>

          <div className="auth-field">
            <label htmlFor="confirm-new-password">
              Confirm New Password
            </label>

            <input
              id="confirm-new-password"
              type="password"
              placeholder="Confirm your new password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              required
              minLength={8}
              autoComplete="new-password"
            />
          </div>

          {error && <div className="auth-error">{error}</div>}

          {message && <div className="auth-message">{message}</div>}

          <button type="submit" className="auth-submit" disabled={loading}>
            {loading ? "Updating..." : "Update Password"}
          </button>
        </form>

        <div className="auth-signup">
          <Link href="/login">Back to Sign In</Link>
        </div>
      </section>
    </main>
  );
}