import type { GetServerSideProps } from 'next'

export const getServerSideProps: GetServerSideProps = async () => ({
  redirect: {
    destination: '/workspace?tab=workbench',
    permanent: false,
  },
})

export default function KnowledgeChatRedirectPage() {
  return null
}
