---
name: nextjs
description: >-
  Best practices and conventions for writing modern Next.js 16+ App Router code.
  Use when writing or reviewing Next.js pages, layouts, Server Components, Client
  Components, Server Actions, Route Handlers, data fetching, caching, or streaming.
metadata:
  version: "16.2.4"
  tags: ["Next.js", "React", "Frontend", "App Router"]
---

# Next.js 16 App Router — Best Practices

## File Conventions (App Router)

All routing lives under `app/`. A route is only publicly accessible when a `page.tsx` or `route.ts` file exists.

| File | Purpose |
|---|---|
| `layout.tsx` | Shared UI that persists across navigation; wraps child segments |
| `page.tsx` | Public route; receives `params` and `searchParams` props |
| `loading.tsx` | Automatic `<Suspense>` wrapper for the route segment |
| `error.tsx` | React error boundary for the segment (must be `'use client'`) |
| `not-found.tsx` | Rendered when `notFound()` is called |
| `route.ts` | API endpoint (exports `GET`, `POST`, etc.) |
| `template.tsx` | Like layout but re-mounts on every navigation |

**Component hierarchy within a segment** (outer → inner):
`layout` → `template` → `error` → `loading` → `not-found` → `page`

## Route Organization

```
app/
  (marketing)/        # Route group — omitted from URL
    page.tsx          → /
  (app)/
    dashboard/
      page.tsx        → /dashboard
    profile/
      page.tsx        → /profile
  blog/
    [slug]/           # Dynamic segment
      page.tsx        → /blog/any-slug
    [...slug]/        # Catch-all
    [[...slug]]/      # Optional catch-all
  _components/        # Private folder — never routable
```

`params` is now a Promise in Next.js 15+: `const { slug } = await params`.

## Server Components vs Client Components

**Default**: all layouts and pages are Server Components. Add `'use client'` only when needed.

Use **Server Components** for:
- Data fetching from databases/APIs (credentials stay on server)
- Reducing client JS bundle size
- First Contentful Paint / streaming

Use **Client Components** (`'use client'`) for:
- `useState`, `useEffect`, event handlers (`onClick`, `onChange`)
- Browser-only APIs (`localStorage`, `window`, `navigator`)
- Custom hooks with state

```tsx
// app/page.tsx — Server Component (default)
import LikeButton from './like-button'

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  const post = await db.getPost(id)          // safe: runs on server
  return <LikeButton likes={post.likes} />   // pass serializable props
}
```

```tsx
// app/like-button.tsx
'use client'
import { useState } from 'react'

export default function LikeButton({ likes }: { likes: number }) {
  const [count, setCount] = useState(likes)
  return <button onClick={() => setCount(c => c + 1)}>{count}</button>
}
```

**Key rules:**
- `'use client'` creates a boundary; all imports below it are also client code
- Props from Server → Client Components must be serializable (no functions, class instances)
- Pass Server Components as `children` to Client Components — they still render on the server
- Use `import 'server-only'` to prevent a module from being imported into client bundles
- Only `NEXT_PUBLIC_` env vars reach the client; others are replaced with empty strings

**Context providers** must be Client Components; wrap only `{children}`, not the whole `<html>`:
```tsx
// app/providers.tsx
'use client'
export function Providers({ children }: { children: React.ReactNode }) {
  return <ThemeContext.Provider value="dark">{children}</ThemeContext.Provider>
}
```

## Data Fetching

### Server Components (preferred)

```tsx
// async component — fetch data directly
export default async function Page() {
  const data = await fetch('https://api.example.com/data')  // no extra library needed
  const json = await data.json()
  return <ul>{json.map(item => <li key={item.id}>{item.name}</li>)}</ul>
}
```

Identical `fetch` calls in the same render pass are automatically **memoized** — fetch the data where you need it rather than drilling props.

### Parallel fetching (avoids waterfalls)

```tsx
export default async function Page({ params }) {
  const { username } = await params
  // kick off both requests immediately
  const [artist, albums] = await Promise.all([
    getArtist(username),
    getAlbums(username),
  ])
  return <>{artist.name} — {albums.length} albums</>
}
```

### Sequential when dependent

```tsx
export default async function Page({ params }) {
  const { username } = await params
  const artist = await getArtist(username)
  return (
    <Suspense fallback={<Skeleton />}>
      <Playlists artistId={artist.id} />   {/* streams in after artist loads */}
    </Suspense>
  )
}
async function Playlists({ artistId }: { artistId: string }) {
  const playlists = await getPlaylists(artistId)
  return <ul>{playlists.map(p => <li key={p.id}>{p.name}</li>)}</ul>
}
```

### Deduplicating non-fetch calls

```ts
// lib/data.ts
import { cache } from 'react'
import 'server-only'

export const getUser = cache(async (id: string) => {
  return db.users.findUnique({ where: { id } })
})
```

### Client Components

Use SWR or TanStack Query for client-side data; or pass an unawaited promise from a Server Component and resolve it with `use()`:

```tsx
// Server Component
const posts = getPosts()   // don't await
return <Suspense fallback={<Skeleton />}><Posts posts={posts} /></Suspense>

// Client Component
'use client'
import { use } from 'react'
export function Posts({ posts }: { posts: Promise<Post[]> }) {
  const data = use(posts)  // suspends until resolved
  return <ul>{data.map(p => <li key={p.id}>{p.title}</li>)}</ul>
}
```

## Streaming

**`loading.tsx`** — wraps the entire page segment in `<Suspense>` automatically:
```tsx
// app/dashboard/loading.tsx
export default function Loading() {
  return <DashboardSkeleton />
}
```

**`<Suspense>`** — for granular streaming within a page:
```tsx
export default function Page() {
  return (
    <>
      <StaticHeader />
      <Suspense fallback={<Skeleton />}>
        <SlowDataComponent />   {/* streams in when ready */}
      </Suspense>
    </>
  )
}
```

Avoid fetching data in layouts that would block navigation — use `<Suspense>` inside `page.tsx` instead.

## Server Actions (Data Mutation)

Server Actions are async functions marked `'use server'` that run on the server and can be called from the client.

### Defining actions

```ts
// app/actions.ts
'use server'
import { auth } from '@/lib/auth'
import { revalidatePath } from 'next/cache'

export async function createPost(formData: FormData) {
  const session = await auth()
  if (!session?.user) throw new Error('Unauthorized')  // always auth-check

  const title = formData.get('title') as string
  await db.posts.create({ data: { title, userId: session.user.id } })
  revalidatePath('/posts')
}
```

Or inline in a Server Component:
```tsx
export default function Page() {
  async function create(formData: FormData) {
    'use server'
    // ...
  }
  return <form action={create}>...</form>
}
```

### Invoking actions

**From forms** (works without JS):
```tsx
<form action={createPost}>
  <input name="title" />
  <button type="submit">Create</button>
</form>
```

**From event handlers** (Client Component):
```tsx
'use client'
import { createPost } from '@/app/actions'

export function CreateButton() {
  return <button onClick={() => createPost(new FormData())}>Create</button>
}
```

**With pending state** (`useActionState`):
```tsx
'use client'
import { useActionState } from 'react'

export function Form() {
  const [state, action, pending] = useActionState(createPost, null)
  return (
    <form action={action}>
      <input name="title" />
      <button disabled={pending}>{pending ? 'Saving…' : 'Save'}</button>
    </form>
  )
}
```

### After mutation

```ts
// revalidate a path
revalidatePath('/posts')

// revalidate by tag
revalidateTag('posts')

// redirect after success
import { redirect } from 'next/navigation'
revalidatePath('/posts')
redirect('/posts')  // throws — must be last call

// refresh current page without revalidating
import { refresh } from 'next/cache'
refresh()
```

**Security**: Server Actions are reachable via direct POST requests — always verify auth/authz inside every action.

## Caching

### fetch caching

```ts
// not cached (default in Next.js 15+)
fetch('https://...')

// cached indefinitely
fetch('https://...', { cache: 'force-cache' })

// time-based revalidation
fetch('https://...', { next: { revalidate: 3600 } })

// tagged for on-demand revalidation
fetch('https://...', { next: { tags: ['user'] } })
```

### Non-fetch caching (`unstable_cache`)

```ts
import { unstable_cache } from 'next/cache'

export const getCachedUser = unstable_cache(
  async (id: string) => db.users.findUnique({ where: { id } }),
  ['user'],                          // cache key prefix
  { tags: ['user'], revalidate: 3600 }
)
```

### Route segment config

```ts
// page.tsx / layout.tsx / route.ts
export const dynamic = 'force-dynamic'   // always dynamic
export const revalidate = 3600           // revalidate every hour
```

### On-demand revalidation

```ts
import { revalidateTag, revalidatePath } from 'next/cache'
revalidateTag('user')       // invalidate all fetches tagged 'user'
revalidatePath('/profile')  // invalidate all cached data for /profile
```

## Route Handlers (API Routes)

```ts
// app/api/posts/route.ts
import { NextRequest, NextResponse } from 'next/server'

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url)
  const id = searchParams.get('id')
  const data = await db.posts.findMany()
  return NextResponse.json(data)
}

export async function POST(request: NextRequest) {
  const body = await request.json()
  const post = await db.posts.create({ data: body })
  return NextResponse.json(post, { status: 201 })
}
```

Dynamic route segments: `app/api/posts/[id]/route.ts`
```ts
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  // ...
}
```

## TypeScript Patterns

```tsx
// Page props types
type PageProps = {
  params: Promise<{ slug: string }>
  searchParams: Promise<{ q?: string }>
}

// Layout props
type LayoutProps = {
  children: React.ReactNode
  params: Promise<{ id: string }>
}
```

## Common Anti-Patterns to Avoid

- **Don't** `await params` outside of async context — `params` is a Promise in Next.js 15+
- **Don't** import server modules (with `process.env` secrets) into Client Components
- **Don't** `await` multiple independent fetches sequentially — use `Promise.all`
- **Don't** add `'use client'` to large layouts — push it down to the specific interactive leaf
- **Don't** call `redirect()` inside try/catch — it throws intentionally
- **Don't** fetch data in layouts if it would block `loading.tsx` from showing
- **Don't** use the `pages/` router for new code — App Router is canonical
