import { useState } from 'react'
import { useFlagsmith } from '@flagsmith/flagsmith/react'
import { createAccount } from './api'

type Props = { onSuccess: (firstName: string) => void }

export function SignupForm({ onSuccess }: Props) {
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const flagsmith = useFlagsmith()

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const data = Object.fromEntries(new FormData(e.currentTarget)) as Record<string, string>
    setSubmitting(true)
    setError(null)
    try {
      await createAccount(data)
      flagsmith.trackEvent('signup_completed')
      onSuccess(data.fullName.split(' ')[0])
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form className="form" onSubmit={handleSubmit}>
      <h2>Create your account</h2>
      <p className="muted">Free for teams up to 10. No credit card.</p>

      <label>
        Full name
        <input name="fullName" autoComplete="name" required />
      </label>
      <label>
        Work email
        <input name="email" type="email" autoComplete="email" required />
      </label>
      <label>
        Password
        <input name="password" type="password" autoComplete="new-password" minLength={8} required />
      </label>

      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={submitting}>
        {submitting ? 'Creating account…' : 'Create account'}
      </button>
    </form>
  )
}