// Stand-in for a real signup API so the demo runs without a backend.
export async function createAccount(data: Record<string, string>): Promise<{ id: string }> {
  await new Promise((resolve) => setTimeout(resolve, 600))
  if (!data.email?.includes('@')) throw new Error('Enter a valid work email.')
  return { id: crypto.randomUUID() }
}
