import type { AppProps } from 'next/app'
import Link from 'next/link'
import { useRouter } from 'next/router'
import '../styles/globals.css'

const NAV_ITEMS = [
  { href: '/workspace', label: '工作区' },
  { href: '/', label: '捕获时间线' },
]

export default function App({ Component, pageProps }: AppProps) {
  const router = useRouter()

  if (router.pathname === '/workspace') {
    return <Component {...pageProps} />
  }

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      {/* Sidebar */}
      <nav className="w-full shrink-0 border-b border-neutral-200 dark:border-neutral-800
        bg-neutral-50 dark:bg-neutral-950 px-3 py-3 md:w-48 md:border-b-0 md:border-r md:py-6">
        <h2 className="text-xs font-medium uppercase tracking-wider text-neutral-400 mb-3 px-2 md:mb-4">
          Hermes
        </h2>
        <ul className="flex gap-1 overflow-x-auto md:flex-col md:overflow-visible">
          {NAV_ITEMS.map(item => (
            <li key={item.href} className="shrink-0 md:shrink">
              <Link
                href={item.href}
                className={`block whitespace-nowrap px-3 py-1.5 text-sm rounded-md transition-colors
                  ${router.pathname === item.href
                    ? 'bg-neutral-200 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 font-medium'
                    : 'text-neutral-600 dark:text-neutral-400 hover:bg-neutral-100 dark:hover:bg-neutral-900'
                  }`}
              >
                {item.label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <Component {...pageProps} />
      </main>
    </div>
  )
}
