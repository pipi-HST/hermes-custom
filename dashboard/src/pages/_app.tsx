import type { AppProps } from 'next/app'
import Link from 'next/link'
import { useRouter } from 'next/router'
import '../styles/globals.css'

const NAV_ITEMS = [
  { href: '/', label: '捕获时间线' },
  { href: '/knowledge-chat', label: '知识工作台' },
  { href: '/drafts', label: '草稿审阅' },
  { href: '/garden', label: '知识卡片' },
]

export default function App({ Component, pageProps }: AppProps) {
  const router = useRouter()

  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <nav className="w-48 shrink-0 border-r border-neutral-200 dark:border-neutral-800
        bg-neutral-50 dark:bg-neutral-950 px-3 py-6">
        <h2 className="text-xs font-medium uppercase tracking-wider text-neutral-400 mb-4 px-2">
          Hermes
        </h2>
        <ul className="flex flex-col gap-1">
          {NAV_ITEMS.map(item => (
            <li key={item.href}>
              <Link
                href={item.href}
                className={`block px-3 py-1.5 text-sm rounded-md transition-colors
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
